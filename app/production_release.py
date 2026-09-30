from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.production_execution_adapter import get_production_execution_adapter


_TRANSITIONS = {
    "SNAPSHOT_CREATED": {"PREPARING", "ABORTED"},
    "PREPARING": {"READY_FOR_PROMOTION", "PREPARE_FAILED"},
    "READY_FOR_PROMOTION": {"ABORTED", "PROMOTION_REQUESTED"},
    "PROMOTION_REQUESTED": {
        "PROMOTION_UNKNOWN",
        "PRODUCTION_ACTIVE",
        "ROLLBACK_REQUIRED",
    },
    "PROMOTION_UNKNOWN": {"PRODUCTION_ACTIVE", "ROLLBACK_REQUIRED"},
    "PRODUCTION_ACTIVE": {"ROLLBACK_REQUIRED"},
    "ROLLBACK_REQUIRED": {"ROLLBACK_REQUESTED"},
    "ROLLBACK_REQUESTED": {"ROLLBACK_UNKNOWN", "ROLLED_BACK"},
    "ROLLBACK_UNKNOWN": {"ROLLED_BACK"},
    "ABORTED": set(),
    "PREPARE_FAILED": set(),
    "ROLLED_BACK": set(),
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _transition_allowed(previous: str, next_status: str) -> bool:
    return next_status in _TRANSITIONS.get(previous, set())


def _normalize_artifact_path(value: str) -> str:
    path = (value or "").strip()
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or path.endswith("/")
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise RuntimeError("Execution bundle contains an unsafe artifact path")
    return path


def _require_step1_disabled() -> None:
    if settings.controlled_production_executor_enabled:
        raise RuntimeError(
            "Step 1 refuses CONTROLLED_PRODUCTION_EXECUTOR_ENABLED=true"
        )
    if settings.production_promotion_enabled:
        raise RuntimeError(
            "Step 1 refuses PRODUCTION_PROMOTION_ENABLED=true"
        )
    if settings.production_rollback_enabled:
        raise RuntimeError(
            "Step 1 refuses PRODUCTION_ROLLBACK_ENABLED=true"
        )
    if settings.production_execution_adapter.strip().upper() != "MOCK":
        raise RuntimeError("Step 1 requires PRODUCTION_EXECUTION_ADAPTER=MOCK")


def _load_authorized_source(db, plan_id):
    return db.execute(text("""
      SELECT dp.*,
             dad.id AS authorization_decision_id,
             dad.decision AS authorization_decision,
             dad.plan_sha256 AS authorization_plan_sha256,
             rc.release_status,
             rc.deployment_enabled AS candidate_deployment_enabled,
             rc.archived_at,
             rrp.run_id,
             rrp.package_status,
             rrp.content_snapshot_complete,
             rrp.package_sha256 AS current_package_sha256,
             rrp.source_tree_sha256 AS current_source_tree_sha256,
             rrp.artifact_manifest
      FROM deployment_plans dp
      JOIN deployment_authorization_decisions dad
        ON dad.deployment_plan_id=dp.id
       AND dad.decision='AUTHORIZE'
      JOIN release_candidates rc
        ON rc.id=dp.release_candidate_id
      JOIN release_review_packages rrp
        ON rrp.id=dp.review_package_id
       AND rrp.release_candidate_id=rc.id
      WHERE dp.id=CAST(:id AS uuid)
      FOR UPDATE OF dp,rc
    """), {"id": plan_id}).mappings().one_or_none()


def _verify_authorized_source(row: dict[str, Any]) -> None:
    if row["plan_status"] != "AUTHORIZED_FOR_DEPLOYMENT":
        raise RuntimeError(
            "Production execution requires AUTHORIZED_FOR_DEPLOYMENT"
        )
    if row["execution_enabled"]:
        raise RuntimeError(
            "Deployment Plan execution_enabled must remain false"
        )
    if row["authorization_decision"] != "AUTHORIZE":
        raise RuntimeError(
            "Production execution requires persisted AUTHORIZE decision"
        )
    if row["authorization_plan_sha256"] != row["plan_sha256"]:
        raise RuntimeError("Deployment Authorization / Plan SHA-256 drifted")
    if row["release_status"] != "RELEASE_APPROVED":
        raise RuntimeError("Release Candidate is not RELEASE_APPROVED")
    if row["candidate_deployment_enabled"]:
        raise RuntimeError(
            "Release Candidate deployment_enabled must remain false"
        )
    if row["archived_at"] is not None:
        raise RuntimeError("Archived Release Candidate cannot execute")
    if (
        row["package_status"] != "GENERATED"
        or row["content_snapshot_complete"] is not True
    ):
        raise RuntimeError(
            "Production execution requires complete immutable Review Package"
        )
    if (
        row["current_package_sha256"] != row["review_package_sha256"]
        or row["current_source_tree_sha256"] != row["source_tree_sha256"]
    ):
        raise RuntimeError("Deployment Plan / Review Package binding drifted")


def _artifact_bundle(db, row: dict[str, Any]) -> dict[str, Any]:
    artifact_rows = [
        dict(item)
        for item in db.execute(text("""
          SELECT sa.relative_path,sa.sha256,sa.byte_size,sa.media_type,
                 sac.content_bytes,sac.content_sha256
          FROM sandbox_artifacts sa
          JOIN sandbox_artifact_contents sac
            ON sac.artifact_id=sa.id
          WHERE sa.run_id=:run_id
          ORDER BY sa.relative_path
        """), {"run_id": row["run_id"]}).mappings().all()
    ]
    if not artifact_rows:
        raise RuntimeError(
            "Execution bundle requires persisted artifact bytes"
        )

    expected_manifest = []
    expected_paths = set()
    for item in list(row["artifact_manifest"] or []):
        path = _normalize_artifact_path(str(item.get("relative_path") or ""))
        if path in expected_paths:
            raise RuntimeError(
                "Review Package artifact manifest contains duplicate paths"
            )
        expected_paths.add(path)
        expected_manifest.append({
            "relative_path": path,
            "sha256": str(item.get("sha256") or "").lower(),
            "byte_size": int(item.get("byte_size") or 0),
            "media_type": str(
                item.get("media_type") or "application/octet-stream"
            ),
        })

    snapshots = []
    seen = set()
    for item in artifact_rows:
        path = _normalize_artifact_path(item["relative_path"])
        if path in seen:
            raise RuntimeError("Execution bundle contains duplicate paths")
        seen.add(path)

        raw = bytes(item["content_bytes"])
        actual_sha = hashlib.sha256(raw).hexdigest()
        recorded_sha = str(item["sha256"]).lower()
        content_sha = str(item["content_sha256"]).lower()
        byte_size = int(item["byte_size"])

        if actual_sha != recorded_sha or content_sha != recorded_sha:
            raise RuntimeError(
                "Execution artifact bytes do not match captured SHA-256"
            )
        if len(raw) != byte_size:
            raise RuntimeError(
                "Execution artifact bytes do not match captured byte size"
            )

        snapshots.append({
            "relative_path": path,
            "sha256": recorded_sha,
            "byte_size": byte_size,
            "media_type": item["media_type"],
            "content_base64": base64.b64encode(raw).decode("ascii"),
        })

    actual_manifest = [
        {
            "relative_path": item["relative_path"],
            "sha256": item["sha256"],
            "byte_size": item["byte_size"],
            "media_type": item["media_type"],
        }
        for item in snapshots
    ]
    if sorted(expected_manifest, key=lambda x: x["relative_path"]) != sorted(
        actual_manifest,
        key=lambda x: x["relative_path"],
    ):
        raise RuntimeError(
            "Execution artifact snapshot does not match Review Package manifest"
        )

    return {
        "schema_version": "production-execution-bundle-v1",
        "review_package_id": str(row["review_package_id"]),
        "review_package_sha256": row["review_package_sha256"],
        "source_tree_sha256": row["source_tree_sha256"],
        "artifacts": snapshots,
    }


def _execution_material(
    row: dict[str, Any],
    *,
    bundle_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "production-release-execution-v1",
        "deployment_plan_id": str(row["id"]),
        "deployment_authorization_decision_id": str(
            row["authorization_decision_id"]
        ),
        "release_candidate_id": str(row["release_candidate_id"]),
        "review_package_id": str(row["review_package_id"]),
        "target_provider": row["target_provider"],
        "target_environment": row["target_environment"],
        "target_project_id": row["target_project_id"],
        "target_team_id": row["target_team_id"],
        "plan_sha256": row["plan_sha256"],
        "review_package_sha256": row["review_package_sha256"],
        "source_tree_sha256": row["source_tree_sha256"],
        "acceptance_provenance_tree_sha256": (
            row["acceptance_provenance_tree_sha256"]
        ),
        "live_acceptance_audit_id": row["live_acceptance_audit_id"],
        "audit_evidence_sha256": row["audit_evidence_sha256"],
        "audit_chain_sha256": row["audit_chain_sha256"],
        "manifest_root_sha256": row["manifest_root_sha256"],
        "chain_head_sha256": row["chain_head_sha256"],
        "source_commit": row["source_commit"],
        "deployment_source_commit": row["deployment_source_commit"],
        "source_vercel_deployment_id": row["source_vercel_deployment_id"],
        "execution_bundle_sha256": bundle_sha256,
        "executor_adapter": "MOCK",
        "production_execution_enabled": False,
        "automatic_execution": False,
        "automatic_promotion": False,
    }


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
    db.execute(text("""
      INSERT INTO production_release_execution_events(
        execution_id,event_type,previous_status,next_status,actor,
        provider_result_sha256,details)
      VALUES(
        CAST(:execution_id AS uuid),:event_type,:previous_status,:next_status,
        :actor,:provider_result_sha256,CAST(:details AS jsonb))
    """), {
        "execution_id": execution_id,
        "event_type": event_type,
        "previous_status": previous_status,
        "next_status": next_status,
        "actor": actor[:200],
        "provider_result_sha256": provider_result_sha256,
        "details": json.dumps(details or {}, ensure_ascii=False),
    })


def create_production_release_execution(
    plan_id,
    *,
    actor: str = "production-release-snapshot",
) -> dict[str, Any]:
    _require_step1_disabled()
    clean_actor = (actor or "production-release-snapshot").strip()
    if not clean_actor:
        clean_actor = "production-release-snapshot"

    with engine.begin() as db:
        source = _load_authorized_source(db, plan_id)
        if source is None:
            raise LookupError("Authorized Deployment Plan not found")
        row = dict(source)
        _verify_authorized_source(row)

        bundle = _artifact_bundle(db, row)
        bundle_sha = _sha256(bundle)
        material = _execution_material(row, bundle_sha256=bundle_sha)
        execution_sha = _sha256(material)

        existing = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE deployment_plan_id=:plan_id
          FOR UPDATE
        """), {"plan_id": row["id"]}).mappings().one_or_none()
        if existing is not None:
            if (
                existing["execution_sha256"] != execution_sha
                or existing["execution_bundle_sha256"] != bundle_sha
            ):
                raise RuntimeError(
                    "Production execution already exists with different immutable material"
                )
            return dict(existing)

        created = db.execute(text("""
          INSERT INTO production_release_executions(
            deployment_plan_id,deployment_authorization_decision_id,
            release_candidate_id,review_package_id,execution_status,
            executor_adapter,target_provider,target_environment,
            target_project_id,target_team_id,plan_sha256,
            review_package_sha256,source_tree_sha256,
            acceptance_provenance_tree_sha256,live_acceptance_audit_id,
            audit_evidence_sha256,audit_chain_sha256,manifest_root_sha256,
            chain_head_sha256,source_commit,deployment_source_commit,
            source_vercel_deployment_id,execution_bundle,
            execution_bundle_sha256,execution_sha256,
            production_execution_enabled,automatic_execution,
            automatic_promotion,created_by)
          VALUES(
            :deployment_plan_id,:authorization_decision_id,
            :release_candidate_id,:review_package_id,'SNAPSHOT_CREATED',
            'MOCK',:target_provider,:target_environment,
            :target_project_id,:target_team_id,:plan_sha256,
            :review_package_sha256,:source_tree_sha256,
            :acceptance_provenance_tree_sha256,:live_acceptance_audit_id,
            :audit_evidence_sha256,:audit_chain_sha256,:manifest_root_sha256,
            :chain_head_sha256,:source_commit,:deployment_source_commit,
            :source_vercel_deployment_id,CAST(:execution_bundle AS jsonb),
            :execution_bundle_sha256,:execution_sha256,
            false,false,false,:created_by)
          RETURNING *
        """), {
            **material,
            "authorization_decision_id": row["authorization_decision_id"],
            "execution_bundle": json.dumps(bundle, ensure_ascii=False),
            "execution_sha256": execution_sha,
            "created_by": clean_actor[:200],
        }).mappings().one()

        _event(
            db,
            created["id"],
            event_type="EXECUTION_SNAPSHOT_CREATED",
            actor=clean_actor,
            previous_status=None,
            next_status="SNAPSHOT_CREATED",
            details={
                "executor_adapter": "MOCK",
                "artifact_count": len(bundle["artifacts"]),
                "external_side_effects": "DENY",
                "production_traffic_changed": False,
            },
        )

    return dict(created)


def prepare_mock_candidate(
    execution_id,
    *,
    actor: str = "mock-production-preparer",
) -> dict[str, Any]:
    _require_step1_disabled()
    clean_actor = (actor or "mock-production-preparer").strip()
    if not clean_actor:
        clean_actor = "mock-production-preparer"

    with engine.begin() as db:
        row = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """), {"id": execution_id}).mappings().one_or_none()
        if row is None:
            raise LookupError("Production release execution not found")
        if row["execution_status"] == "READY_FOR_PROMOTION":
            return dict(row)
        if row["execution_status"] != "SNAPSHOT_CREATED":
            raise RuntimeError(
                "MOCK prepare requires SNAPSHOT_CREATED execution"
            )
        if row["executor_adapter"] != "MOCK":
            raise RuntimeError("Step 1 only permits MOCK adapter")

        db.execute(text("""
          UPDATE production_release_executions
          SET execution_status='PREPARING'
          WHERE id=:id
        """), {"id": row["id"]})
        _event(
            db,
            row["id"],
            event_type="PREPARE_REQUESTED",
            actor=clean_actor,
            previous_status="SNAPSHOT_CREATED",
            next_status="PREPARING",
            details={
                "adapter": "MOCK",
                "provider_write_performed": False,
            },
        )
        snapshot = dict(row)
        snapshot["execution_status"] = "PREPARING"

    adapter = get_production_execution_adapter("MOCK")
    try:
        candidate = adapter.prepare_candidate(snapshot)
        if candidate.provider_write_performed:
            raise RuntimeError(
                "Step 1 MOCK adapter unexpectedly reported a provider write"
            )
        if candidate.state != "READY":
            raise RuntimeError("MOCK candidate did not reach READY")
    except Exception as exc:
        with engine.begin() as db:
            current = db.execute(text("""
              SELECT execution_status
              FROM production_release_executions
              WHERE id=CAST(:id AS uuid)
              FOR UPDATE
            """), {"id": execution_id}).mappings().one()
            if current["execution_status"] == "PREPARING":
                db.execute(text("""
                  UPDATE production_release_executions
                  SET execution_status='PREPARE_FAILED'
                  WHERE id=CAST(:id AS uuid)
                """), {"id": execution_id})
                _event(
                    db,
                    execution_id,
                    event_type="FAILURE",
                    actor=clean_actor,
                    previous_status="PREPARING",
                    next_status="PREPARE_FAILED",
                    details={
                        "adapter": "MOCK",
                        "error_type": type(exc).__name__,
                        "provider_write_performed": False,
                    },
                )
        raise

    provider_result = {
        "deployment_id": candidate.deployment_id,
        "url": candidate.url,
        "state": candidate.state,
        "provider_write_performed": candidate.provider_write_performed,
        "metadata": candidate.metadata,
    }
    provider_result_sha = _sha256(provider_result)

    with engine.begin() as db:
        current = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """), {"id": execution_id}).mappings().one()
        if current["execution_status"] != "PREPARING":
            raise RuntimeError(
                "Execution state changed during MOCK preparation"
            )

        prepared = db.execute(text("""
          UPDATE production_release_executions
          SET execution_status='READY_FOR_PROMOTION',
              candidate_vercel_deployment_id=:deployment_id,
              candidate_vercel_url=:url,
              prepared_at=now(),
              production_execution_enabled=false,
              automatic_execution=false,
              automatic_promotion=false
          WHERE id=CAST(:id AS uuid)
          RETURNING *
        """), {
            "id": execution_id,
            "deployment_id": candidate.deployment_id,
            "url": candidate.url,
        }).mappings().one()

        _event(
            db,
            execution_id,
            event_type="CANDIDATE_CREATED",
            actor=clean_actor,
            previous_status="PREPARING",
            next_status="PREPARING",
            details={
                "adapter": "MOCK",
                "deployment_id": candidate.deployment_id,
                "provider_write_performed": False,
            },
            provider_result_sha256=provider_result_sha,
        )
        _event(
            db,
            execution_id,
            event_type="CANDIDATE_READY",
            actor=clean_actor,
            previous_status="PREPARING",
            next_status="READY_FOR_PROMOTION",
            details={
                "adapter": "MOCK",
                "deployment_id": candidate.deployment_id,
                "state": candidate.state,
                "production_traffic_changed": False,
            },
            provider_result_sha256=provider_result_sha,
        )
        _event(
            db,
            execution_id,
            event_type="CANDIDATE_VERIFIED",
            actor=clean_actor,
            previous_status="READY_FOR_PROMOTION",
            next_status="READY_FOR_PROMOTION",
            details={
                "adapter": "MOCK",
                "external_side_effects": "DENY",
                "production_traffic_changed": False,
            },
            provider_result_sha256=provider_result_sha,
        )

    return dict(prepared)


def get_production_release_execution(execution_id) -> dict[str, Any]:
    with engine.connect() as db:
        row = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
        """), {"id": execution_id}).mappings().one_or_none()
        if row is None:
            raise LookupError("Production release execution not found")

        decisions = [
            dict(item)
            for item in db.execute(text("""
              SELECT id,decision,reason,actor,execution_sha256,decided_at
              FROM production_release_execution_decisions
              WHERE execution_id=:id
              ORDER BY decided_at,id
            """), {"id": row["id"]}).mappings().all()
        ]
        events = [
            dict(item)
            for item in db.execute(text("""
              SELECT id,sequence_no,event_type,previous_status,next_status,actor,
                     provider_result_sha256,details,created_at
              FROM production_release_execution_events
              WHERE execution_id=:id
              ORDER BY sequence_no
            """), {"id": row["id"]}).mappings().all()
        ]

    result = dict(row)
    result["decisions"] = decisions
    result["events"] = events
    return result
