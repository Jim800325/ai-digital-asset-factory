from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text

from app.db import engine
from app.providers.animation.shrimp.bilibili_live_publisher import (
    BilibiliLivePublisherAdapter,
)
from app.providers.animation.shrimp.bilibili_quota import (
    settle_cleanup,
)
from app.providers.animation.shrimp.bilibili_credentials import (
    build_adapter_for_execution,
    preflight_recheck_execution_credential,
)
from app.providers.animation.shrimp.publisher_execution import (
    get_publish_execution,
    publish_uploaded_media,
    reconcile_publish_upload,
    reconcile_published_media,
    upload_publish_media,
)
from app.providers.animation.shrimp.publisher_execution_adapter import (
    PublisherWriteOutcomeUnknown,
    PublisherWriteRejected,
)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _serialize(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("id", "execution_id"):
        if key in result and result[key] is not None:
            result[key] = str(result[key])
    return result


def _expected_mid(adapter, execution: dict[str, Any]) -> str:
    resolver=getattr(adapter,"expected_mid",None)
    if callable(resolver):
        return str(resolver(execution))
    return str(execution.get("account_reference") or "")


def _marker(upload_idempotency_key: str) -> str:
    return f"[shrimp-step10b:{upload_idempotency_key}]"


def _get_run_locked(db, execution_id) -> dict[str, Any] | None:
    row = db.execute(
        text("""
          SELECT *
          FROM shrimp_animation_bilibili_live_acceptance_runs
          WHERE execution_id=CAST(:execution_id AS uuid)
          FOR UPDATE
        """),
        {"execution_id": execution_id},
    ).mappings().one_or_none()
    return dict(row) if row else None


def _merge_evidence(
    current: dict[str, Any] | None,
    section: str,
    value: Any,
) -> dict[str, Any]:
    result = dict(current or {})
    result[section] = value
    return result


def _upsert_failure(
    execution_id,
    *,
    actor: str,
    exc: Exception,
) -> None:
    evidence_sha = _sha({
        "error_type": type(exc).__name__,
        "message": str(exc)[:2000],
    })
    with engine.begin() as db:
        run = _get_run_locked(db, execution_id)
        if run is None or run["acceptance_status"] == "CLEANED_UP":
            return
        db.execute(
            text("""
              UPDATE shrimp_animation_bilibili_live_acceptance_runs
              SET acceptance_status='FAILED',
                  failure_type=:failure_type,
                  failure_evidence_sha256=:failure_sha,
                  evidence=CAST(:evidence AS jsonb),
                  finished_at=now()
              WHERE id=:id
            """),
            {
                "id": run["id"],
                "failure_type": type(exc).__name__[:200],
                "failure_sha": evidence_sha,
                "evidence": _json(
                    _merge_evidence(
                        run.get("evidence"),
                        "failure",
                        {
                            "actor": actor,
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:2000],
                            "evidence_sha256": evidence_sha,
                        },
                    )
                ),
            },
        )


def get_bilibili_live_acceptance(execution_id) -> dict[str, Any] | None:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_bilibili_live_acceptance_runs
              WHERE execution_id=CAST(:execution_id AS uuid)
            """),
            {"execution_id": execution_id},
        ).mappings().one_or_none()
    return _serialize(row) if row else None


def _cleanup_once(
    execution_id,
    *,
    execution: dict[str, Any],
    aid: str,
    actor: str,
    adapter: BilibiliLivePublisherAdapter,
) -> dict[str, Any]:
    with engine.begin() as db:
        run = _get_run_locked(db, execution_id)
        if run is None:
            raise RuntimeError("Step 10B acceptance audit is unavailable")
        if run["cleanup_verified"]:
            return {
                "deleted": True,
                "write_performed": False,
                "replayed": True,
                "cleanup_outcome": run["cleanup_outcome"],
            }
        if run["cleanup_write_count"] == 0:
            db.execute(
                text("""
                  UPDATE shrimp_animation_bilibili_live_acceptance_runs
                  SET cleanup_write_count=1,
                      cleanup_outcome='REQUESTED'
                  WHERE id=:id
                """),
                {"id": run["id"]},
            )
            do_cleanup = True
        else:
            do_cleanup = False

    cleanup_ambiguous = False
    if do_cleanup:
        try:
            cleanup_result = adapter.delete_archive(
                execution,
                aid=aid,
            )
            with engine.begin() as db:
                run = _get_run_locked(db, execution_id)
                db.execute(
                    text("""
                      UPDATE shrimp_animation_bilibili_live_acceptance_runs
                      SET cleanup_outcome='ACCEPTED',
                          cleanup_performed=true,
                          evidence=CAST(:evidence AS jsonb)
                      WHERE id=:id
                    """),
                    {
                        "id": run["id"],
                        "evidence": _json(
                            _merge_evidence(
                                run.get("evidence"),
                                "cleanup_write",
                                {
                                    **cleanup_result,
                                    "actor": actor,
                                },
                            )
                        ),
                    },
                )
        except PublisherWriteOutcomeUnknown as exc:
            cleanup_ambiguous = True
            with engine.begin() as db:
                run = _get_run_locked(db, execution_id)
                db.execute(
                    text("""
                      UPDATE shrimp_animation_bilibili_live_acceptance_runs
                      SET acceptance_status='CLEANUP_UNKNOWN',
                          cleanup_outcome='AMBIGUOUS',
                          failure_type=:failure_type,
                          failure_evidence_sha256=:failure_sha,
                          evidence=CAST(:evidence AS jsonb)
                      WHERE id=:id
                    """),
                    {
                        "id": run["id"],
                        "failure_type": exc.error_type[:200],
                        "failure_sha": exc.evidence_sha256,
                        "evidence": _json(
                            _merge_evidence(
                                run.get("evidence"),
                                "cleanup_ambiguous",
                                {
                                    "actor": actor,
                                    "error_type": exc.error_type,
                                    "evidence_sha256":
                                        exc.evidence_sha256,
                                    "write_replay_forbidden": True,
                                },
                            )
                        ),
                    },
                )
        except PublisherWriteRejected as exc:
            with engine.begin() as db:
                run = _get_run_locked(db, execution_id)
                db.execute(
                    text("""
                      UPDATE shrimp_animation_bilibili_live_acceptance_runs
                      SET cleanup_outcome='REJECTED',
                          failure_type='PublisherWriteRejected',
                          failure_evidence_sha256=:failure_sha,
                          evidence=CAST(:evidence AS jsonb)
                      WHERE id=:id
                    """),
                    {
                        "id": run["id"],
                        "failure_sha": exc.evidence_sha256,
                        "evidence": _json(
                            _merge_evidence(
                                run.get("evidence"),
                                "cleanup_rejected",
                                {
                                    "actor": actor,
                                    "evidence_sha256":
                                        exc.evidence_sha256,
                                    "message": str(exc)[:2000],
                                },
                            )
                        ),
                    },
                )
            raise

    deleted = adapter.verify_deleted(execution, aid=aid)
    with engine.begin() as db:
        run = _get_run_locked(db, execution_id)
        if deleted:
            outcome = (
                "RECONCILED_DELETED"
                if cleanup_ambiguous
                else run["cleanup_outcome"]
            )
            db.execute(
                text("""
                  UPDATE shrimp_animation_bilibili_live_acceptance_runs
                  SET cleanup_outcome=:cleanup_outcome,
                      cleanup_verified=true,
                      cleanup_performed=true,
                      evidence=CAST(:evidence AS jsonb)
                  WHERE id=:id
                """),
                {
                    "id": run["id"],
                    "cleanup_outcome": outcome,
                    "evidence": _json(
                        _merge_evidence(
                            run.get("evidence"),
                            "cleanup_read_back",
                            {
                                "aid": aid,
                                "deleted": True,
                                "provider_write_replayed": False,
                            },
                        )
                    ),
                },
            )
        else:
            db.execute(
                text("""
                  UPDATE shrimp_animation_bilibili_live_acceptance_runs
                  SET acceptance_status='CLEANUP_UNKNOWN',
                      cleanup_outcome='RECONCILED_PRESENT',
                      evidence=CAST(:evidence AS jsonb)
                  WHERE id=:id
                """),
                {
                    "id": run["id"],
                    "evidence": _json(
                        _merge_evidence(
                            run.get("evidence"),
                            "cleanup_read_back",
                            {
                                "aid": aid,
                                "deleted": False,
                                "second_delete_forbidden": True,
                            },
                        )
                    ),
                },
            )
    if deleted:
        settle_cleanup(
            execution_id=execution_id,
            source_sha256=_sha({
                "execution_id":str(execution_id),
                "aid":str(aid),
                "cleanup_verified":True,
            }),
            actor=actor,
        )
    return {
        "deleted": bool(deleted),
        "write_performed": bool(do_cleanup),
        "replayed": not do_cleanup,
        "cleanup_outcome": (
            get_bilibili_live_acceptance(execution_id) or {}
        ).get("cleanup_outcome"),
    }


def run_bilibili_live_acceptance(
    execution_id,
    *,
    actor: str = "shrimp-bilibili-live-acceptance-api",
    adapter: BilibiliLivePublisherAdapter | None = None,
) -> dict[str, Any]:
    clean_actor = (
        (actor or "").strip() or "shrimp-bilibili-live-acceptance-api"
    )
    execution = get_publish_execution(execution_id)
    if adapter is None:
        preflight_recheck_execution_credential(
            execution,
            actor=clean_actor + "-preflight-recheck",
        )
        execution = get_publish_execution(execution_id)
    chosen = adapter or build_adapter_for_execution(execution)

    if execution["platform"] != "BILIBILI":
        raise RuntimeError("Step 10B only accepts BILIBILI executions")
    if execution["execution_adapter"] != "BILIBILI_CONTROLLED":
        raise RuntimeError(
            "Step 10B requires execution_adapter=BILIBILI_CONTROLLED"
        )
    if execution["source_stale"]:
        raise RuntimeError("Step 10B refuses to begin from a STALE source")

    marker = _marker(execution["upload_idempotency_key"])

    with engine.begin() as db:
        run = _get_run_locked(db, execution_id)
        if run is None:
            row = db.execute(
                text("""
                  INSERT INTO shrimp_animation_bilibili_live_acceptance_runs(
                    execution_id,acceptance_status,expected_mid,
                    reconciliation_marker,evidence,created_by)
                  VALUES(
                    CAST(:execution_id AS uuid),'CREATED',:expected_mid,
                    :marker,'{}'::jsonb,:actor)
                  RETURNING *
                """),
                {
                    "execution_id": execution_id,
                    "expected_mid": _expected_mid(chosen, execution),
                    "marker": marker,
                    "actor": clean_actor[:200],
                },
            ).mappings().one()
            run = dict(row)
        elif run["acceptance_status"] == "CLEANED_UP":
            result = _serialize(run)
            result["replayed"] = True
            return result

    try:
        preflight = chosen.preflight(execution)
        if preflight["mid"] != _expected_mid(chosen, execution):
            raise RuntimeError(
                "Step 10B authenticated MID does not match execution"
            )

        with engine.begin() as db:
            run = _get_run_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_bilibili_live_acceptance_runs
                  SET acceptance_status='PRECHECKED',
                      actual_mid=:actual_mid,
                      evidence=CAST(:evidence AS jsonb)
                  WHERE id=:id
                """),
                {
                    "id": run["id"],
                    "actual_mid": preflight["mid"],
                    "evidence": _json(
                        _merge_evidence(
                            run.get("evidence"),
                            "preflight",
                            {
                                "mid": preflight["mid"],
                                "uname": preflight.get("uname"),
                                "is_login": preflight.get("is_login"),
                                "private_only": True,
                            },
                        )
                    ),
                },
            )

        execution = get_publish_execution(execution_id)
        if execution["execution_status"] == "SNAPSHOT_CREATED":
            try:
                upload_publish_media(
                    execution_id,
                    actor=clean_actor,
                    adapter=chosen,
                )
            except RuntimeError:
                execution = get_publish_execution(execution_id)
                if execution["execution_status"] != "UPLOAD_UNKNOWN":
                    raise
                reconcile_publish_upload(
                    execution_id,
                    actor=clean_actor,
                    adapter=chosen,
                )

        execution = get_publish_execution(execution_id)
        if execution["execution_status"] == "UPLOAD_UNKNOWN":
            reconcile_publish_upload(
                execution_id,
                actor=clean_actor,
                adapter=chosen,
            )
            execution = get_publish_execution(execution_id)

        if execution["execution_status"] != "UPLOADED":
            raise RuntimeError(
                "Step 10B upload did not reconcile to UPLOADED"
            )
        aid = str(execution.get("provider_upload_id") or "")
        if not aid:
            raise RuntimeError("Step 10B upload has no Bilibili AID")

        with engine.begin() as db:
            run = _get_run_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_bilibili_live_acceptance_runs
                  SET acceptance_status='UPLOADED',
                      aid=COALESCE(aid,:aid),
                      external_upload_performed=true,
                      evidence=CAST(:evidence AS jsonb)
                  WHERE id=:id
                """),
                {
                    "id": run["id"],
                    "aid": int(aid),
                    "evidence": _json(
                        _merge_evidence(
                            run.get("evidence"),
                            "upload",
                            {
                                "aid": aid,
                                "upload_write_count":
                                    execution["upload_write_count"],
                                "upload_outcome":
                                    execution["upload_outcome"],
                            },
                        )
                    ),
                },
            )

        if execution["execution_status"] == "UPLOADED":
            try:
                publish_uploaded_media(
                    execution_id,
                    actor=clean_actor,
                    adapter=chosen,
                )
            except RuntimeError:
                execution = get_publish_execution(execution_id)
                if execution["execution_status"] != "PUBLISH_UNKNOWN":
                    raise
                reconcile_published_media(
                    execution_id,
                    actor=clean_actor,
                    adapter=chosen,
                )

        execution = get_publish_execution(execution_id)
        if execution["execution_status"] == "PUBLISH_UNKNOWN":
            reconcile_published_media(
                execution_id,
                actor=clean_actor,
                adapter=chosen,
            )
            execution = get_publish_execution(execution_id)

        if execution["execution_status"] != "PUBLISHED":
            raise RuntimeError(
                "Step 10B publish did not reconcile to PUBLISHED"
            )

        aid = str(
            execution.get("provider_publish_id")
            or execution.get("provider_upload_id")
            or ""
        )
        if not aid:
            raise RuntimeError("Step 10B publish has no Bilibili AID")

        with engine.begin() as db:
            run = _get_run_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_bilibili_live_acceptance_runs
                  SET acceptance_status='PUBLISHED',
                      aid=COALESCE(aid,:aid),
                      provider_video_url=COALESCE(
                        provider_video_url,:provider_video_url
                      ),
                      external_publish_performed=true,
                      evidence=CAST(:evidence AS jsonb)
                  WHERE id=:id
                """),
                {
                    "id": run["id"],
                    "aid": int(aid),
                    "provider_video_url":
                        execution.get("provider_publish_url"),
                    "evidence": _json(
                        _merge_evidence(
                            run.get("evidence"),
                            "publish",
                            {
                                "aid": aid,
                                "publish_write_count":
                                    execution["publish_write_count"],
                                "publish_outcome":
                                    execution["publish_outcome"],
                                "provider_video_url":
                                    execution.get("provider_publish_url"),
                            },
                        )
                    ),
                },
            )

        readback = chosen.read_back_archive(
            execution,
            aid=aid,
            marker=marker,
        )
        if int(readback["is_only_self"]) != 1:
            raise RuntimeError(
                "Step 10B provider read-back is not only-self/private"
            )

        with engine.begin() as db:
            run = _get_run_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_bilibili_live_acceptance_runs
                  SET acceptance_status='VERIFIED_PRIVATE',
                      bvid=COALESCE(bvid,:bvid),
                      archive_state=:archive_state,
                      is_only_self=1,
                      provider_read_back_verified=true,
                      private_visibility_verified=true,
                      evidence=CAST(:evidence AS jsonb)
                  WHERE id=:id
                """),
                {
                    "id": run["id"],
                    "bvid": readback.get("bvid") or None,
                    "archive_state": str(readback.get("state_desc") or ""),
                    "evidence": _json(
                        _merge_evidence(
                            run.get("evidence"),
                            "read_back",
                            {
                                **readback,
                                "marker_verified": True,
                            },
                        )
                    ),
                },
            )

        cleanup = _cleanup_once(
            execution_id,
            execution=execution,
            aid=aid,
            actor=clean_actor,
            adapter=chosen,
        )

        if cleanup["deleted"]:
            with engine.begin() as db:
                run = _get_run_locked(db, execution_id)
                db.execute(
                    text("""
                      UPDATE shrimp_animation_bilibili_live_acceptance_runs
                      SET acceptance_status='CLEANED_UP',
                          finished_at=now()
                      WHERE id=:id
                    """),
                    {"id": run["id"]},
                )

        result = get_bilibili_live_acceptance(execution_id)
        if result is None:
            raise RuntimeError("Step 10B acceptance audit disappeared")
        result["replayed"] = False
        return result

    except Exception as exc:
        try:
            current_execution = get_publish_execution(execution_id)
            current_run = get_bilibili_live_acceptance(execution_id)
            emergency_aid = str(
                (current_run or {}).get("aid")
                or current_execution.get("provider_publish_id")
                or current_execution.get("provider_upload_id")
                or ""
            )
            if emergency_aid:
                _cleanup_once(
                    execution_id,
                    execution=current_execution,
                    aid=emergency_aid,
                    actor=clean_actor + "-emergency-cleanup",
                    adapter=chosen,
                )
        except Exception:
            pass
        _upsert_failure(
            execution_id,
            actor=clean_actor,
            exc=exc,
        )
        raise
