from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.human_review import (
    get_episode_bundle_file,
    get_episode_player_file,
    get_review_document_file,
)
from app.providers.animation.shrimp.publisher_execution_adapter import (
    PublishReceipt,
    PublisherExecutionAdapter,
    PublisherWriteOutcomeUnknown,
    PublisherWriteRejected,
    UploadReceipt,
    get_publisher_execution_adapter,
    sha256_json,
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _serialize(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in (
        "id",
        "publish_plan_id",
        "authorization_decision_id",
        "provider_job_id",
        "target_id",
        "execution_id",
    ):
        if key in result and result[key] is not None:
            result[key] = str(result[key])
    return result


def _event(
    db,
    execution_id,
    *,
    event_type: str,
    actor: str,
    previous_status: str | None,
    next_status: str | None,
    details: dict[str, Any] | None = None,
    provider_result_sha256: str | None = None,
) -> None:
    db.execute(
        text("""
          INSERT INTO shrimp_animation_publish_execution_events(
            execution_id,event_type,previous_status,next_status,actor,
            provider_result_sha256,details)
          VALUES(
            CAST(:execution_id AS uuid),:event_type,:previous_status,
            :next_status,:actor,:provider_result_sha256,
            CAST(:details AS jsonb))
        """),
        {
            "execution_id": execution_id,
            "event_type": event_type,
            "previous_status": previous_status,
            "next_status": next_status,
            "actor": actor[:200],
            "provider_result_sha256": provider_result_sha256,
            "details": canonical_json(details or {}),
        },
    )


def _configured_adapter_kind() -> str:
    return (
        settings.shrimp_publish_execution_adapter.strip().upper()
        or "MOCK"
    )


def _target_policy(
    *,
    adapter_kind: str,
    platform: str,
    target_key: str,
    account_reference: str,
) -> None:
    if adapter_kind == "MOCK":
        return

    if not settings.shrimp_publish_executor_enabled:
        raise RuntimeError(
            "Controlled Publisher live executor is disabled"
        )

    expected = {
        "BILIBILI_CONTROLLED": "BILIBILI",
        "YOUTUBE_CONTROLLED": "YOUTUBE",
    }.get(adapter_kind)
    if expected is None:
        raise RuntimeError("Unsupported controlled publisher adapter")
    if platform != expected:
        raise RuntimeError(
            f"{adapter_kind} cannot execute platform={platform}"
        )

    allowed_accounts = set(
        settings.shrimp_publish_execution_allowed_account_ref_list
    )
    denied_accounts = set(
        settings.shrimp_publish_execution_denied_account_ref_list
    )
    allowed_targets = set(
        settings.shrimp_publish_execution_allowed_target_key_list
    )
    denied_targets = set(
        settings.shrimp_publish_execution_denied_target_key_list
    )

    if not allowed_accounts:
        raise RuntimeError(
            "Sacrificial publisher account allowlist is empty"
        )
    if not denied_accounts:
        raise RuntimeError(
            "Real publisher account denylist is empty"
        )
    if not allowed_targets:
        raise RuntimeError(
            "Sacrificial publisher target allowlist is empty"
        )

    if allowed_accounts & denied_accounts:
        raise RuntimeError(
            "Publisher account allowlist overlaps denylist"
        )
    if allowed_targets & denied_targets:
        raise RuntimeError(
            "Publisher target allowlist overlaps denylist"
        )
    if account_reference in denied_accounts:
        raise RuntimeError("Publisher account is denylisted")
    if target_key in denied_targets:
        raise RuntimeError("Publisher target is denylisted")
    if account_reference not in allowed_accounts:
        raise RuntimeError(
            "Publisher account is not sacrificial-account allowlisted"
        )
    if target_key not in allowed_targets:
        raise RuntimeError(
            "Publisher target is not sacrificial-target allowlisted"
        )


def _current_authorized_plan(db, plan_id) -> dict[str, Any]:
    row = db.execute(
        text("""
          SELECT pp.*,
                 pad.id AS authorization_decision_id,
                 pad.decision AS authorization_decision,
                 pad.decision_sha256 AS authorization_decision_sha256,
                 pad.decision_status AS authorization_decision_status,
                 pad.plan_sha256 AS authorization_plan_sha256,
                 pad.dry_run_sha256 AS authorization_dry_run_sha256,
                 pt.account_reference,
                 pt.display_name AS target_display_name,
                 pt.metadata_constraints AS current_metadata_constraints,
                 pt.target_status,
                 pt.execution_enabled AS target_execution_enabled,
                 pt.external_publish_enabled,
                 rd.decision AS review_decision,
                 rd.decision_sha256 AS current_review_decision_sha256,
                 rd.decision_status AS review_decision_status,
                 saj.review_status,
                 saj.episode_bundle_sha256 AS current_bundle_sha256,
                 saj.release_review_package_sha256 AS current_review_sha256,
                 pj.job_status,
                 pj.publish_enabled,
                 pj.production_execution_enabled,
                 pj.external_side_effects
          FROM shrimp_animation_publish_plans pp
          JOIN shrimp_animation_publish_authorization_decisions pad
            ON pad.publish_plan_id=pp.id
           AND pad.decision_status='CURRENT'
          JOIN shrimp_animation_publish_targets pt ON pt.id=pp.target_id
          JOIN shrimp_animation_review_decisions rd
            ON rd.id=pp.review_decision_id
          JOIN shrimp_animation_jobs saj
            ON saj.provider_job_id=pp.provider_job_id
          JOIN production_provider_jobs pj
            ON pj.id=pp.provider_job_id
          WHERE pp.id=CAST(:plan_id AS uuid)
          FOR UPDATE OF pp,pad,pt,rd,saj,pj
        """),
        {"plan_id": plan_id},
    ).mappings().one_or_none()
    if row is None:
        raise LookupError("PUBLISH_AUTHORIZED Publisher Plan not found")

    result = dict(row)
    if result["plan_status"] != "PUBLISH_AUTHORIZED":
        raise RuntimeError(
            "Controlled Publisher Execution requires PUBLISH_AUTHORIZED"
        )
    if (
        result["authorization_decision"] != "AUTHORIZE"
        or result["authorization_decision_status"] != "CURRENT"
    ):
        raise RuntimeError(
            "Controlled Publisher Execution requires current AUTHORIZE decision"
        )
    if (
        result["authorization_plan_sha256"] != result["plan_sha256"]
        or result["authorization_dry_run_sha256"] != result["dry_run_sha256"]
    ):
        raise RuntimeError("Publish Authorization / Plan binding drifted")
    if (
        result["review_decision"] != "APPROVE"
        or result["review_decision_status"] != "CURRENT"
    ):
        raise RuntimeError("Step 8 APPROVE decision is no longer current")
    if (
        result["current_review_decision_sha256"]
        != result["review_decision_sha256"]
    ):
        raise RuntimeError("Step 8 review decision SHA-256 drifted")
    if result["review_status"] != "RELEASE_APPROVED":
        raise RuntimeError("Episode is no longer RELEASE_APPROVED")
    if result["job_status"] != "QC_PASSED":
        raise RuntimeError("Episode is no longer QC_PASSED")
    if result["publish_enabled"] or result["production_execution_enabled"]:
        raise RuntimeError("Provider job execution safety invariant failed")
    if result["external_side_effects"] != "DENY":
        raise RuntimeError("Provider job external side effects are not DENY")
    if (
        result["current_bundle_sha256"] != result["episode_bundle_sha256"]
        or result["current_review_sha256"]
        != result["release_review_package_sha256"]
    ):
        raise RuntimeError("Step 8 artifact hash binding drifted")
    if (
        result["target_status"] != "ACTIVE"
        or result["target_execution_enabled"]
        or result["external_publish_enabled"]
    ):
        raise RuntimeError("Publish Target is not execution-safe")
    if not str(result["account_reference"] or "").strip():
        raise RuntimeError(
            "Controlled Publisher Execution requires exact account_reference"
        )
    return result


def _verify_step8_files(job_id, expected: dict[str, Any]) -> dict[str, Any]:
    bundle = get_episode_bundle_file(job_id)
    review = get_review_document_file(job_id)
    episode = get_episode_player_file(job_id)
    if bundle.sha256 != expected["episode_bundle_sha256"]:
        raise RuntimeError("Episode Bundle bytes drifted")
    if episode.sha256 != expected["render_artifact_sha256"]:
        raise RuntimeError("Episode MP4 bytes drifted")
    # release_review_package_sha256 identifies the complete Review Package,
    # while this endpoint verifies the frozen Markdown artifact bytes.
    return {
        "bundle_sha256": bundle.sha256,
        "review_document_sha256": review.sha256,
        "render_artifact_sha256": episode.sha256,
        "media_path": episode.path,
    }


def _target_snapshot_sha(row: dict[str, Any]) -> str:
    material = {
        "schema_version": "shrimp-publish-target-v0.1",
        "target_key": row["target_key"],
        "platform": row["platform"],
        "display_name": row["target_display_name"],
        "account_reference": row["account_reference"],
        "metadata_constraints": dict(
            row["current_metadata_constraints"] or {}
        ),
        "target_status": row["target_status"],
        "execution_enabled": False,
        "external_publish_enabled": False,
    }
    return sha256_json(material)


def create_publish_execution(
    plan_id,
    *,
    actor: str = "shrimp-publish-execution-api",
    adapter_kind: str | None = None,
) -> dict[str, Any]:
    clean_actor = (
        (actor or "").strip() or "shrimp-publish-execution-api"
    )
    kind = (adapter_kind or _configured_adapter_kind()).upper().strip()
    if kind not in {
        "MOCK",
        "BILIBILI_CONTROLLED",
        "YOUTUBE_CONTROLLED",
    }:
        raise RuntimeError("Unsupported controlled publisher adapter")

    with engine.begin() as db:
        source = _current_authorized_plan(db, plan_id)
        _target_policy(
            adapter_kind=kind,
            platform=source["platform"],
            target_key=source["target_key"],
            account_reference=source["account_reference"],
        )
        if _target_snapshot_sha(source) != source["target_snapshot_sha256"]:
            raise RuntimeError("Publish Target snapshot drifted")

        existing = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_executions
              WHERE publish_plan_id=:plan_id
              FOR UPDATE
            """),
            {"plan_id": source["id"]},
        ).mappings().one_or_none()
        if existing is not None:
            result = _serialize(existing)
            result["replayed"] = True
            return result

        media = get_episode_player_file(source["provider_job_id"])
        bundle = get_episode_bundle_file(source["provider_job_id"])
        get_review_document_file(source["provider_job_id"])
        if bundle.sha256 != source["episode_bundle_sha256"]:
            raise RuntimeError("Episode Bundle bytes drifted before execution")

        execution_material = {
            "schema_version": "shrimp-publisher-execution-v0.1",
            "publish_plan_id": str(source["id"]),
            "authorization_decision_id": str(
                source["authorization_decision_id"]
            ),
            "provider_job_id": str(source["provider_job_id"]),
            "target_id": str(source["target_id"]),
            "platform": source["platform"],
            "target_key": source["target_key"],
            "account_reference": source["account_reference"],
            "plan_sha256": source["plan_sha256"],
            "dry_run_sha256": source["dry_run_sha256"],
            "authorization_decision_sha256":
                source["authorization_decision_sha256"],
            "review_decision_sha256": source["review_decision_sha256"],
            "episode_bundle_sha256": source["episode_bundle_sha256"],
            "release_review_package_sha256":
                source["release_review_package_sha256"],
            "render_artifact_sha256": media.sha256,
            "target_snapshot_sha256": source["target_snapshot_sha256"],
            "publish_metadata": dict(source["publish_metadata"] or {}),
            "execution_adapter": kind,
            "automatic_execution": False,
        }
        execution_sha = sha256_json(execution_material)
        upload_key = _sha256_text(
            "shrimp-publisher-upload-v0.1|" + execution_sha
        )
        publish_key = _sha256_text(
            "shrimp-publisher-publish-v0.1|" + execution_sha
        )

        row = db.execute(
            text("""
              INSERT INTO shrimp_animation_publish_executions(
                publish_plan_id,authorization_decision_id,provider_job_id,
                target_id,platform,target_key,account_reference,
                plan_sha256,dry_run_sha256,authorization_decision_sha256,
                review_decision_sha256,episode_bundle_sha256,
                release_review_package_sha256,render_artifact_sha256,
                target_snapshot_sha256,publish_metadata,execution_adapter,
                execution_sha256,upload_idempotency_key,
                publish_idempotency_key,execution_status,
                automatic_execution,created_by)
              VALUES(
                :publish_plan_id,:authorization_decision_id,:provider_job_id,
                :target_id,:platform,:target_key,:account_reference,
                :plan_sha256,:dry_run_sha256,:authorization_decision_sha256,
                :review_decision_sha256,:episode_bundle_sha256,
                :release_review_package_sha256,:render_artifact_sha256,
                :target_snapshot_sha256,CAST(:publish_metadata AS jsonb),
                :execution_adapter,:execution_sha256,:upload_key,
                :publish_key,'SNAPSHOT_CREATED',false,:actor)
              RETURNING *
            """),
            {
                **execution_material,
                "publish_metadata": canonical_json(
                    execution_material["publish_metadata"]
                ),
                "execution_sha256": execution_sha,
                "upload_key": upload_key,
                "publish_key": publish_key,
                "actor": clean_actor[:200],
            },
        ).mappings().one()
        _event(
            db,
            row["id"],
            event_type="SNAPSHOT_CREATED",
            actor=clean_actor,
            previous_status=None,
            next_status="SNAPSHOT_CREATED",
            details={
                "execution_sha256": execution_sha,
                "adapter": kind,
                "provider_write_performed": False,
            },
        )

    result = _serialize(row)
    result["replayed"] = False
    result["provider_write_performed"] = False
    result["external_publish_performed"] = False
    return result


def _get_execution_locked(db, execution_id) -> dict[str, Any]:
    row = db.execute(
        text("""
          SELECT e.*,
                 pp.plan_status AS current_plan_status,
                 pp.plan_sha256 AS current_plan_sha256,
                 pp.dry_run_sha256 AS current_dry_run_sha256,
                 pad.decision AS current_authorization_decision,
                 pad.decision_status AS current_authorization_status,
                 pad.decision_sha256 AS current_authorization_sha256,
                 pt.target_status AS current_target_status,
                 pt.execution_enabled AS target_execution_enabled,
                 pt.external_publish_enabled AS target_external_publish_enabled
          FROM shrimp_animation_publish_executions e
          JOIN shrimp_animation_publish_plans pp
            ON pp.id=e.publish_plan_id
          JOIN shrimp_animation_publish_authorization_decisions pad
            ON pad.id=e.authorization_decision_id
          JOIN shrimp_animation_publish_targets pt
            ON pt.id=e.target_id
          WHERE e.id=CAST(:execution_id AS uuid)
          FOR UPDATE OF e,pp,pad,pt
        """),
        {"execution_id": execution_id},
    ).mappings().one_or_none()
    if row is None:
        raise LookupError("Controlled Publisher Execution not found")
    return dict(row)


def _verify_new_write_source(
    row: dict[str, Any],
    *,
    adapter: PublisherExecutionAdapter,
) -> Path:
    if row["source_stale"]:
        raise RuntimeError(
            "Execution source is STALE; new provider writes are forbidden"
        )
    if row["current_plan_status"] != "PUBLISH_AUTHORIZED":
        raise RuntimeError("Publisher Plan is no longer PUBLISH_AUTHORIZED")
    if (
        row["current_authorization_decision"] != "AUTHORIZE"
        or row["current_authorization_status"] != "CURRENT"
        or row["current_authorization_sha256"]
        != row["authorization_decision_sha256"]
    ):
        raise RuntimeError("Publish authorization is no longer current")
    if (
        row["current_plan_sha256"] != row["plan_sha256"]
        or row["current_dry_run_sha256"] != row["dry_run_sha256"]
    ):
        raise RuntimeError("Publisher Plan identity drifted")
    if (
        row["current_target_status"] != "ACTIVE"
        or row["target_execution_enabled"]
        or row["target_external_publish_enabled"]
    ):
        raise RuntimeError("Publish Target is no longer execution-safe")

    _target_policy(
        adapter_kind=row["execution_adapter"],
        platform=row["platform"],
        target_key=row["target_key"],
        account_reference=row["account_reference"],
    )
    adapter.validate_target(row)

    media = get_episode_player_file(row["provider_job_id"])
    get_episode_bundle_file(row["provider_job_id"])
    get_review_document_file(row["provider_job_id"])
    if media.sha256 != row["render_artifact_sha256"]:
        raise RuntimeError("Episode MP4 SHA-256 drifted")
    return media.path


def _upload_request_sha(row: dict[str, Any]) -> str:
    return sha256_json({
        "schema_version": "shrimp-publisher-upload-request-v0.1",
        "execution_sha256": row["execution_sha256"],
        "platform": row["platform"],
        "target_key": row["target_key"],
        "account_reference": row["account_reference"],
        "render_artifact_sha256": row["render_artifact_sha256"],
        "idempotency_key": row["upload_idempotency_key"],
    })


def _publish_request_sha(row: dict[str, Any]) -> str:
    return sha256_json({
        "schema_version": "shrimp-publisher-publish-request-v0.1",
        "execution_sha256": row["execution_sha256"],
        "provider_upload_id": row["provider_upload_id"],
        "publish_metadata": dict(row["publish_metadata"] or {}),
        "idempotency_key": row["publish_idempotency_key"],
    })


def _receipt_sha(receipt: UploadReceipt | PublishReceipt) -> str:
    return sha256_json({
        key: value
        for key, value in receipt.__dict__.items()
    })


def upload_publish_media(
    execution_id,
    *,
    actor: str = "shrimp-publish-execution-api",
    adapter: PublisherExecutionAdapter | None = None,
) -> dict[str, Any]:
    clean_actor = (
        (actor or "").strip() or "shrimp-publish-execution-api"
    )

    with engine.begin() as db:
        row = _get_execution_locked(db, execution_id)
        if row["execution_status"] == "UPLOADED":
            result = _serialize(row)
            result["replayed"] = True
            return result
        if row["upload_write_count"] >= 1:
            raise RuntimeError(
                "Upload provider write budget is exhausted; reconcile instead of retry"
            )
        if row["execution_status"] != "SNAPSHOT_CREATED":
            raise RuntimeError("Upload requires SNAPSHOT_CREATED")
        chosen = adapter or get_publisher_execution_adapter(
            row["execution_adapter"]
        )
        media_path = _verify_new_write_source(row, adapter=chosen)
        request_sha = _upload_request_sha(row)
        db.execute(
            text("""
              UPDATE shrimp_animation_publish_executions
              SET execution_status='UPLOADING',
                  upload_outcome='REQUESTED',
                  upload_write_count=1,
                  upload_request_sha256=:request_sha,
                  upload_attempted_at=now()
              WHERE id=:id
            """),
            {"id": row["id"], "request_sha": request_sha},
        )
        _event(
            db,
            row["id"],
            event_type="UPLOAD_REQUESTED",
            actor=clean_actor,
            previous_status="SNAPSHOT_CREATED",
            next_status="UPLOADING",
            details={
                "request_sha256": request_sha,
                "write_count": 1,
                "idempotency_key": row["upload_idempotency_key"],
            },
        )

    row["upload_request_sha256"] = request_sha
    try:
        receipt = chosen.upload(
            row,
            media_path=Path(media_path),
            idempotency_key=row["upload_idempotency_key"],
        )
    except PublisherWriteOutcomeUnknown as exc:
        with engine.begin() as db:
            current = _get_execution_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_publish_executions
                  SET execution_status='UPLOAD_UNKNOWN',
                      upload_outcome='AMBIGUOUS',
                      upload_last_error_type=:error_type,
                      upload_last_error_sha256=:error_sha
                  WHERE id=:id
                """),
                {
                    "id": current["id"],
                    "error_type": exc.error_type[:200],
                    "error_sha": exc.evidence_sha256,
                },
            )
            _event(
                db,
                current["id"],
                event_type="UPLOAD_AMBIGUOUS",
                actor=clean_actor,
                previous_status="UPLOADING",
                next_status="UPLOAD_UNKNOWN",
                details={
                    "error_type": exc.error_type,
                    "reconcile_required": True,
                    "provider_write_replay_forbidden": True,
                },
                provider_result_sha256=exc.evidence_sha256,
            )
        raise RuntimeError(
            "Publisher upload outcome is unknown; read-only reconciliation is required"
        ) from exc
    except (PublisherWriteRejected, RuntimeError) as exc:
        evidence = (
            exc.evidence_sha256
            if isinstance(exc, PublisherWriteRejected)
            else sha256_json({
                "error_type": type(exc).__name__,
                "message": str(exc)[:2000],
            })
        )
        with engine.begin() as db:
            current = _get_execution_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_publish_executions
                  SET execution_status='UPLOAD_FAILED',
                      upload_outcome='REJECTED',
                      upload_last_error_type=:error_type,
                      upload_last_error_sha256=:error_sha
                  WHERE id=:id
                """),
                {
                    "id": current["id"],
                    "error_type": type(exc).__name__[:200],
                    "error_sha": evidence,
                },
            )
            _event(
                db,
                current["id"],
                event_type="UPLOAD_REJECTED",
                actor=clean_actor,
                previous_status="UPLOADING",
                next_status="UPLOAD_FAILED",
                details={"message": str(exc)[:2000]},
                provider_result_sha256=evidence,
            )
        raise RuntimeError(str(exc)) from exc

    result_sha = _receipt_sha(receipt)
    if not receipt.provider_upload_id:
        raise RuntimeError("Publisher upload returned no provider upload ID")

    with engine.begin() as db:
        current = _get_execution_locked(db, execution_id)
        db.execute(
            text("""
              UPDATE shrimp_animation_publish_executions
              SET execution_status='UPLOADED',
                  upload_outcome='ACCEPTED',
                  provider_upload_id=:provider_upload_id,
                  upload_provider_state=:provider_state,
                  upload_provider_result_sha256=:result_sha
              WHERE id=:id
            """),
            {
                "id": current["id"],
                "provider_upload_id": receipt.provider_upload_id,
                "provider_state": receipt.state[:200],
                "result_sha": result_sha,
            },
        )
        _event(
            db,
            current["id"],
            event_type="UPLOAD_ACCEPTED",
            actor=clean_actor,
            previous_status="UPLOADING",
            next_status="UPLOADED",
            details={
                "provider_upload_id": receipt.provider_upload_id,
                "provider_state": receipt.state,
                "provider_write_performed":
                    receipt.provider_write_performed,
            },
            provider_result_sha256=result_sha,
        )

    return get_publish_execution(execution_id)


def reconcile_publish_upload(
    execution_id,
    *,
    actor: str = "shrimp-publish-reconcile-api",
    adapter: PublisherExecutionAdapter | None = None,
) -> dict[str, Any]:
    clean_actor = (
        (actor or "").strip() or "shrimp-publish-reconcile-api"
    )
    with engine.connect() as db:
        row = _get_execution_locked(db, execution_id)
    if row["upload_write_count"] != 1:
        raise RuntimeError("Upload reconciliation requires exactly one write attempt")
    if row["execution_status"] not in {"UPLOADING", "UPLOAD_UNKNOWN"}:
        raise RuntimeError(
            "Upload reconciliation requires UPLOADING or UPLOAD_UNKNOWN"
        )
    chosen = adapter or get_publisher_execution_adapter(
        row["execution_adapter"]
    )
    receipt = chosen.reconcile_upload(
        row,
        idempotency_key=row["upload_idempotency_key"],
        provider_upload_id=row["provider_upload_id"],
    )

    with engine.begin() as db:
        current = _get_execution_locked(db, execution_id)
        if receipt is None:
            previous = current["execution_status"]
            db.execute(
                text("""
                  UPDATE shrimp_animation_publish_executions
                  SET execution_status='UPLOAD_UNKNOWN',
                      upload_outcome='RECONCILED_PENDING',
                      upload_reconciled_at=now()
                  WHERE id=:id
                """),
                {"id": current["id"]},
            )
            _event(
                db,
                current["id"],
                event_type="UPLOAD_RECONCILED_PENDING",
                actor=clean_actor,
                previous_status=previous,
                next_status="UPLOAD_UNKNOWN",
                details={
                    "provider_write_performed": False,
                    "provider_write_replay_forbidden": True,
                },
            )
        else:
            result_sha = _receipt_sha(receipt)
            previous = current["execution_status"]
            db.execute(
                text("""
                  UPDATE shrimp_animation_publish_executions
                  SET execution_status='UPLOADED',
                      upload_outcome='RECONCILED_ACCEPTED',
                      provider_upload_id=COALESCE(
                        provider_upload_id,:provider_upload_id
                      ),
                      upload_provider_state=:provider_state,
                      upload_provider_result_sha256=:result_sha,
                      upload_reconciled_at=now()
                  WHERE id=:id
                """),
                {
                    "id": current["id"],
                    "provider_upload_id": receipt.provider_upload_id,
                    "provider_state": receipt.state[:200],
                    "result_sha": result_sha,
                },
            )
            _event(
                db,
                current["id"],
                event_type="UPLOAD_RECONCILED_ACCEPTED",
                actor=clean_actor,
                previous_status=previous,
                next_status="UPLOADED",
                details={
                    "provider_upload_id": receipt.provider_upload_id,
                    "provider_write_performed": False,
                },
                provider_result_sha256=result_sha,
            )
    return get_publish_execution(execution_id)


def publish_uploaded_media(
    execution_id,
    *,
    actor: str = "shrimp-publish-execution-api",
    adapter: PublisherExecutionAdapter | None = None,
) -> dict[str, Any]:
    clean_actor = (
        (actor or "").strip() or "shrimp-publish-execution-api"
    )

    with engine.begin() as db:
        row = _get_execution_locked(db, execution_id)
        if row["execution_status"] == "PUBLISHED":
            result = _serialize(row)
            result["replayed"] = True
            return result
        if row["publish_write_count"] >= 1:
            raise RuntimeError(
                "Publish provider write budget is exhausted; reconcile instead of retry"
            )
        if row["execution_status"] != "UPLOADED":
            raise RuntimeError("Publish requires UPLOADED")
        if not row["provider_upload_id"]:
            raise RuntimeError("Publish requires provider upload ID")
        chosen = adapter or get_publisher_execution_adapter(
            row["execution_adapter"]
        )
        _verify_new_write_source(row, adapter=chosen)
        request_sha = _publish_request_sha(row)
        db.execute(
            text("""
              UPDATE shrimp_animation_publish_executions
              SET execution_status='PUBLISHING',
                  publish_outcome='REQUESTED',
                  publish_write_count=1,
                  publish_request_sha256=:request_sha,
                  publish_attempted_at=now()
              WHERE id=:id
            """),
            {"id": row["id"], "request_sha": request_sha},
        )
        _event(
            db,
            row["id"],
            event_type="PUBLISH_REQUESTED",
            actor=clean_actor,
            previous_status="UPLOADED",
            next_status="PUBLISHING",
            details={
                "request_sha256": request_sha,
                "write_count": 1,
                "idempotency_key": row["publish_idempotency_key"],
            },
        )

    row["publish_request_sha256"] = request_sha
    try:
        receipt = chosen.publish(
            row,
            provider_upload_id=row["provider_upload_id"],
            idempotency_key=row["publish_idempotency_key"],
        )
    except PublisherWriteOutcomeUnknown as exc:
        with engine.begin() as db:
            current = _get_execution_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_publish_executions
                  SET execution_status='PUBLISH_UNKNOWN',
                      publish_outcome='AMBIGUOUS',
                      publish_last_error_type=:error_type,
                      publish_last_error_sha256=:error_sha
                  WHERE id=:id
                """),
                {
                    "id": current["id"],
                    "error_type": exc.error_type[:200],
                    "error_sha": exc.evidence_sha256,
                },
            )
            _event(
                db,
                current["id"],
                event_type="PUBLISH_AMBIGUOUS",
                actor=clean_actor,
                previous_status="PUBLISHING",
                next_status="PUBLISH_UNKNOWN",
                details={
                    "error_type": exc.error_type,
                    "reconcile_required": True,
                    "provider_write_replay_forbidden": True,
                },
                provider_result_sha256=exc.evidence_sha256,
            )
        raise RuntimeError(
            "Publisher publish outcome is unknown; read-only reconciliation is required"
        ) from exc
    except (PublisherWriteRejected, RuntimeError) as exc:
        evidence = (
            exc.evidence_sha256
            if isinstance(exc, PublisherWriteRejected)
            else sha256_json({
                "error_type": type(exc).__name__,
                "message": str(exc)[:2000],
            })
        )
        with engine.begin() as db:
            current = _get_execution_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_publish_executions
                  SET execution_status='PUBLISH_FAILED',
                      publish_outcome='REJECTED',
                      publish_last_error_type=:error_type,
                      publish_last_error_sha256=:error_sha
                  WHERE id=:id
                """),
                {
                    "id": current["id"],
                    "error_type": type(exc).__name__[:200],
                    "error_sha": evidence,
                },
            )
            _event(
                db,
                current["id"],
                event_type="PUBLISH_REJECTED",
                actor=clean_actor,
                previous_status="PUBLISHING",
                next_status="PUBLISH_FAILED",
                details={"message": str(exc)[:2000]},
                provider_result_sha256=evidence,
            )
        raise RuntimeError(str(exc)) from exc

    result_sha = _receipt_sha(receipt)
    if not receipt.provider_publish_id:
        raise RuntimeError("Publisher publish returned no provider publish ID")

    with engine.begin() as db:
        current = _get_execution_locked(db, execution_id)
        db.execute(
            text("""
              UPDATE shrimp_animation_publish_executions
              SET execution_status='PUBLISHED',
                  publish_outcome='ACCEPTED',
                  provider_publish_id=:provider_publish_id,
                  provider_publish_url=:provider_publish_url,
                  publish_provider_state=:provider_state,
                  publish_provider_result_sha256=:result_sha,
                  external_publish_performed=:external_performed
              WHERE id=:id
            """),
            {
                "id": current["id"],
                "provider_publish_id": receipt.provider_publish_id,
                "provider_publish_url": receipt.url,
                "provider_state": receipt.state[:200],
                "result_sha": result_sha,
                "external_performed": bool(
                    receipt.provider_write_performed
                ),
            },
        )
        _event(
            db,
            current["id"],
            event_type="PUBLISH_ACCEPTED",
            actor=clean_actor,
            previous_status="PUBLISHING",
            next_status="PUBLISHED",
            details={
                "provider_publish_id": receipt.provider_publish_id,
                "provider_publish_url": receipt.url,
                "provider_state": receipt.state,
                "provider_write_performed":
                    receipt.provider_write_performed,
            },
            provider_result_sha256=result_sha,
        )
    return get_publish_execution(execution_id)


def reconcile_published_media(
    execution_id,
    *,
    actor: str = "shrimp-publish-reconcile-api",
    adapter: PublisherExecutionAdapter | None = None,
) -> dict[str, Any]:
    clean_actor = (
        (actor or "").strip() or "shrimp-publish-reconcile-api"
    )
    with engine.connect() as db:
        row = _get_execution_locked(db, execution_id)
    if row["publish_write_count"] != 1:
        raise RuntimeError(
            "Publish reconciliation requires exactly one write attempt"
        )
    if row["execution_status"] not in {"PUBLISHING", "PUBLISH_UNKNOWN"}:
        raise RuntimeError(
            "Publish reconciliation requires PUBLISHING or PUBLISH_UNKNOWN"
        )
    chosen = adapter or get_publisher_execution_adapter(
        row["execution_adapter"]
    )
    receipt = chosen.reconcile_publish(
        row,
        idempotency_key=row["publish_idempotency_key"],
        provider_publish_id=row["provider_publish_id"],
    )

    with engine.begin() as db:
        current = _get_execution_locked(db, execution_id)
        if receipt is None:
            previous = current["execution_status"]
            db.execute(
                text("""
                  UPDATE shrimp_animation_publish_executions
                  SET execution_status='PUBLISH_UNKNOWN',
                      publish_outcome='RECONCILED_PENDING',
                      publish_reconciled_at=now()
                  WHERE id=:id
                """),
                {"id": current["id"]},
            )
            _event(
                db,
                current["id"],
                event_type="PUBLISH_RECONCILED_PENDING",
                actor=clean_actor,
                previous_status=previous,
                next_status="PUBLISH_UNKNOWN",
                details={
                    "provider_write_performed": False,
                    "provider_write_replay_forbidden": True,
                },
            )
        else:
            result_sha = _receipt_sha(receipt)
            previous = current["execution_status"]
            db.execute(
                text("""
                  UPDATE shrimp_animation_publish_executions
                  SET execution_status='PUBLISHED',
                      publish_outcome='RECONCILED_ACCEPTED',
                      provider_publish_id=COALESCE(
                        provider_publish_id,:provider_publish_id
                      ),
                      provider_publish_url=COALESCE(
                        provider_publish_url,:provider_publish_url
                      ),
                      publish_provider_state=:provider_state,
                      publish_provider_result_sha256=:result_sha,
                      publish_reconciled_at=now(),
                      external_publish_performed=:external_performed
                  WHERE id=:id
                """),
                {
                    "id": current["id"],
                    "provider_publish_id": receipt.provider_publish_id,
                    "provider_publish_url": receipt.url,
                    "provider_state": receipt.state[:200],
                    "result_sha": result_sha,
                    "external_performed": bool(
                        receipt.provider_write_performed
                    ),
                },
            )
            _event(
                db,
                current["id"],
                event_type="PUBLISH_RECONCILED_ACCEPTED",
                actor=clean_actor,
                previous_status=previous,
                next_status="PUBLISHED",
                details={
                    "provider_publish_id": receipt.provider_publish_id,
                    "provider_publish_url": receipt.url,
                    "provider_write_performed": False,
                },
                provider_result_sha256=result_sha,
            )
    return get_publish_execution(execution_id)


def get_publish_execution(execution_id) -> dict[str, Any]:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_executions
              WHERE id=CAST(:execution_id AS uuid)
            """),
            {"execution_id": execution_id},
        ).mappings().one_or_none()
        if row is None:
            raise LookupError("Controlled Publisher Execution not found")
        events = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_execution_events
              WHERE execution_id=:execution_id
              ORDER BY created_at,id
            """),
            {"execution_id": row["id"]},
        ).mappings().all()
    result = _serialize(row)
    result["events"] = [_serialize(item) for item in events]
    return result


def get_publish_execution_for_plan(plan_id) -> dict[str, Any] | None:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT id
              FROM shrimp_animation_publish_executions
              WHERE publish_plan_id=CAST(:plan_id AS uuid)
            """),
            {"plan_id": plan_id},
        ).mappings().one_or_none()
    if row is None:
        return None
    return get_publish_execution(row["id"])


def list_publish_executions(job_id) -> list[dict[str, Any]]:
    with engine.connect() as db:
        rows = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_executions
              WHERE provider_job_id=CAST(:job_id AS uuid)
              ORDER BY created_at DESC,id DESC
            """),
            {"job_id": job_id},
        ).mappings().all()
    return [_serialize(row) for row in rows]
