from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.live_acceptance_registry import live_acceptance_integrity_manifest
from app.production_execution_adapter import (
    ProviderPrepareRejected,
    ProviderWriteOutcomeUnknown,
    VercelControlledExecutionAdapter,
    get_production_execution_adapter,
)
from app.release_integrity_gate import evaluate_release_integrity


_TRANSITIONS = {
    "SNAPSHOT_CREATED": {"PREPARING", "ABORTED"},
    "PREPARING": {"PREPARE_UNKNOWN", "READY_FOR_PROMOTION", "PREPARE_FAILED"},
    "PREPARE_UNKNOWN": {"READY_FOR_PROMOTION", "PREPARE_FAILED"},
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


def _configured_execution_adapter() -> str:
    return (
        settings.production_execution_adapter.strip().upper()
        or "MOCK"
    )


def _require_step1_disabled() -> None:
    adapter = _configured_execution_adapter()
    if settings.production_promotion_enabled:
        raise RuntimeError(
            "Controlled PREPARE refuses PRODUCTION_PROMOTION_ENABLED=true"
        )
    if settings.production_rollback_enabled:
        raise RuntimeError(
            "Controlled PREPARE refuses PRODUCTION_ROLLBACK_ENABLED=true"
        )

    if adapter == "MOCK":
        if settings.controlled_production_executor_enabled:
            raise RuntimeError(
                "MOCK mode requires CONTROLLED_PRODUCTION_EXECUTOR_ENABLED=false"
            )
        return

    if adapter == "VERCEL_CONTROLLED_EXECUTOR":
        if not settings.controlled_production_executor_enabled:
            raise RuntimeError(
                "Vercel controlled PREPARE requires "
                "CONTROLLED_PRODUCTION_EXECUTOR_ENABLED=true"
            )
        return

    raise RuntimeError(
        "Unsupported PRODUCTION_EXECUTION_ADAPTER for controlled release"
    )


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
    executor_adapter: str,
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
        "executor_adapter": executor_adapter,
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



def _execution_snapshot_material(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "production-release-execution-v1",
        "deployment_plan_id": str(row["deployment_plan_id"]),
        "deployment_authorization_decision_id": str(
            row["deployment_authorization_decision_id"]
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
        "execution_bundle_sha256": row["execution_bundle_sha256"],
        "executor_adapter": row["executor_adapter"],
        "production_execution_enabled": bool(
            row["production_execution_enabled"]
        ),
        "automatic_execution": bool(row["automatic_execution"]),
        "automatic_promotion": bool(row["automatic_promotion"]),
    }


def _validate_execution_bundle_snapshot(
    row: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    bundle = row.get("execution_bundle")
    if not isinstance(bundle, dict):
        return ["execution_bundle_not_object"]

    if bundle.get("schema_version") != "production-execution-bundle-v1":
        reasons.append("execution_bundle_schema_invalid")
    if str(bundle.get("review_package_id") or "") != str(
        row["review_package_id"]
    ):
        reasons.append("execution_bundle_review_package_id_drift")
    if bundle.get("review_package_sha256") != row["review_package_sha256"]:
        reasons.append("execution_bundle_review_package_sha256_drift")
    if bundle.get("source_tree_sha256") != row["source_tree_sha256"]:
        reasons.append("execution_bundle_source_tree_sha256_drift")

    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        reasons.append("execution_bundle_artifacts_missing")
        artifacts = []

    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            reasons.append("execution_bundle_artifact_invalid")
            continue
        try:
            path = _normalize_artifact_path(
                str(item.get("relative_path") or "")
            )
        except RuntimeError:
            reasons.append("execution_bundle_artifact_path_invalid")
            continue
        if path in seen:
            reasons.append("execution_bundle_artifact_path_duplicate")
            continue
        seen.add(path)

        recorded_sha = str(item.get("sha256") or "").lower()
        try:
            recorded_size = int(item.get("byte_size"))
        except (TypeError, ValueError):
            reasons.append("execution_bundle_artifact_size_invalid")
            continue

        encoded = item.get("content_base64")
        if not isinstance(encoded, str):
            reasons.append("execution_bundle_artifact_bytes_missing")
            continue
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError):
            reasons.append("execution_bundle_artifact_base64_invalid")
            continue

        if hashlib.sha256(raw).hexdigest() != recorded_sha:
            reasons.append("execution_bundle_artifact_sha256_mismatch")
        if len(raw) != recorded_size:
            reasons.append("execution_bundle_artifact_byte_size_mismatch")

    if _sha256(bundle) != str(row["execution_bundle_sha256"]).lower():
        reasons.append("execution_bundle_sha256_mismatch")

    return list(dict.fromkeys(reasons))


def _stored_chain_is_verified_ancestor(
    manifest: dict[str, Any],
    stored_chain_head_sha256: str | None,
) -> bool:
    stored = (stored_chain_head_sha256 or "").strip().lower()
    if len(stored) != 64:
        return False
    if (
        manifest.get("status") != "VERIFIED"
        or manifest.get("manifest_root_valid") is not True
        or manifest.get("chain_valid") is not True
    ):
        return False

    current = str(manifest.get("chain_head_sha256") or "").strip().lower()
    if current == stored:
        return True

    entries = manifest.get("entries")
    if not isinstance(entries, dict):
        return False
    for item in entries.values():
        if not isinstance(item, dict):
            continue
        if (
            str(item.get("chain_sha256") or "").strip().lower() == stored
            and item.get("status") == "VERIFIED"
        ):
            return True
    return False


def _execution_integrity_locked(
    db,
    execution_row: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    row = dict(execution_row)
    reasons = _validate_execution_bundle_snapshot(row)

    expected_execution_sha = _sha256(_execution_snapshot_material(row))
    if expected_execution_sha != str(row["execution_sha256"]).lower():
        reasons.append("execution_sha256_mismatch")

    if row["executor_adapter"] not in {
        "MOCK",
        "VERCEL_CONTROLLED_EXECUTOR",
    }:
        reasons.append("executor_adapter_unsupported")
    if row["executor_adapter"] == "VERCEL_CONTROLLED_EXECUTOR":
        try:
            get_production_execution_adapter(
                "VERCEL_CONTROLLED_EXECUTOR"
            ).validate_target(row)
        except RuntimeError as exc:
            reasons.append(
                "vercel_target_policy_" + str(exc).lower().replace(" ", "_")[:160]
            )
    if row["production_execution_enabled"]:
        reasons.append("production_execution_enabled_unexpectedly")
    if row["automatic_execution"]:
        reasons.append("automatic_execution_enabled_unexpectedly")
    if row["automatic_promotion"]:
        reasons.append("automatic_promotion_enabled_unexpectedly")

    source = _load_authorized_source(db, row["deployment_plan_id"])
    integrity: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    current_bundle_sha: str | None = None

    if source is None:
        reasons.append("authorized_source_missing")
    else:
        source_row = dict(source)
        try:
            _verify_authorized_source(source_row)
        except RuntimeError:
            reasons.append("authorized_source_invalid")

        bindings = (
            (
                "deployment_authorization_decision_id_drift",
                str(row["deployment_authorization_decision_id"]),
                str(source_row["authorization_decision_id"]),
            ),
            (
                "release_candidate_id_drift",
                str(row["release_candidate_id"]),
                str(source_row["release_candidate_id"]),
            ),
            (
                "review_package_id_drift",
                str(row["review_package_id"]),
                str(source_row["review_package_id"]),
            ),
            (
                "target_provider_drift",
                row["target_provider"],
                source_row["target_provider"],
            ),
            (
                "target_environment_drift",
                row["target_environment"],
                source_row["target_environment"],
            ),
            (
                "target_project_id_drift",
                row["target_project_id"],
                source_row["target_project_id"],
            ),
            (
                "target_team_id_drift",
                row["target_team_id"],
                source_row["target_team_id"],
            ),
            (
                "plan_sha256_drift",
                row["plan_sha256"],
                source_row["plan_sha256"],
            ),
            (
                "review_package_sha256_drift",
                row["review_package_sha256"],
                source_row["review_package_sha256"],
            ),
            (
                "source_tree_sha256_drift",
                row["source_tree_sha256"],
                source_row["source_tree_sha256"],
            ),
        )
        for reason, frozen, current in bindings:
            if frozen != current:
                reasons.append(reason)

        try:
            current_bundle = _artifact_bundle(db, source_row)
            current_bundle_sha = _sha256(current_bundle)
            if current_bundle_sha != row["execution_bundle_sha256"]:
                reasons.append("persisted_artifact_bundle_drift")
        except RuntimeError:
            reasons.append("persisted_artifact_bundle_invalid")

        integrity = evaluate_release_integrity(
            source_row["current_source_tree_sha256"],
            list(source_row["artifact_manifest"] or []),
        )
        if integrity.get("allowed") is not True:
            reasons.extend(
                "release_integrity_" + item
                for item in (
                    integrity.get("blocking_reasons")
                    or ["not_verified"]
                )
            )

        integrity_bindings = (
            (
                "acceptance_provenance_tree_sha256_drift",
                row["acceptance_provenance_tree_sha256"],
                integrity.get("acceptance_provenance_tree_sha256"),
            ),
            (
                "live_acceptance_audit_id_drift",
                str(row["live_acceptance_audit_id"]),
                str(integrity.get("audit_id") or ""),
            ),
            (
                "audit_evidence_sha256_drift",
                row["audit_evidence_sha256"],
                integrity.get("audit_evidence_sha256"),
            ),
            (
                "audit_chain_sha256_drift",
                row["audit_chain_sha256"],
                integrity.get("audit_chain_sha256"),
            ),
            (
                "source_commit_drift",
                row["source_commit"],
                integrity.get("source_commit"),
            ),
            (
                "deployment_source_commit_drift",
                row["deployment_source_commit"],
                integrity.get("deployment_source_commit"),
            ),
            (
                "source_vercel_deployment_id_drift",
                row["source_vercel_deployment_id"],
                integrity.get("vercel_deployment_id"),
            ),
        )
        for reason, frozen, current in integrity_bindings:
            if frozen != current:
                reasons.append(reason)

        manifest = live_acceptance_integrity_manifest()
        if manifest.get("status") != "VERIFIED":
            reasons.append("integrity_manifest_not_verified")
        if manifest.get("manifest_root_valid") is not True:
            reasons.append("integrity_manifest_root_invalid")
        if manifest.get("chain_valid") is not True:
            reasons.append("integrity_manifest_chain_invalid")

    stored_chain_is_ancestor = _stored_chain_is_verified_ancestor(
        manifest,
        row.get("chain_head_sha256"),
    )
    if not stored_chain_is_ancestor:
        reasons.append("stored_chain_head_not_verified_ancestor")

    current_chain_head = manifest.get("chain_head_sha256")
    current_manifest_root = manifest.get("manifest_root_sha256")
    chain_extended = (
        stored_chain_is_ancestor
        and bool(current_chain_head)
        and current_chain_head != row.get("chain_head_sha256")
    )

    reasons = list(dict.fromkeys(reasons))
    check_status = "VERIFIED" if not reasons else "BLOCKED"
    check_id = db.execute(text("""
      INSERT INTO production_release_execution_integrity_checks(
        execution_id,check_status,actor,execution_sha256,
        blocking_reasons,current_manifest_root_sha256,
        current_chain_head_sha256,stored_chain_head_is_ancestor,
        registry_chain_extended,external_side_effects,
        production_traffic_changed)
      VALUES(
        :execution_id,:check_status,:actor,:execution_sha256,
        CAST(:blocking_reasons AS jsonb),:manifest_root,:chain_head,
        :stored_chain_is_ancestor,:chain_extended,'DENY',false)
      RETURNING id
    """), {
        "execution_id": row["id"],
        "check_status": check_status,
        "actor": actor[:200],
        "execution_sha256": row["execution_sha256"],
        "blocking_reasons": json.dumps(reasons, ensure_ascii=False),
        "manifest_root": current_manifest_root,
        "chain_head": current_chain_head,
        "stored_chain_is_ancestor": stored_chain_is_ancestor,
        "chain_extended": chain_extended,
    }).scalar_one()

    return {
        "allowed": not reasons,
        "integrity_status": check_status,
        "integrity_check_id": str(check_id),
        "execution_id": str(row["id"]),
        "execution_sha256": row["execution_sha256"],
        "blocking_reasons": reasons,
        "current_manifest_root_sha256": current_manifest_root,
        "current_chain_head_sha256": current_chain_head,
        "stored_chain_head_is_ancestor": stored_chain_is_ancestor,
        "registry_chain_extended": chain_extended,
        "current_execution_bundle_sha256": current_bundle_sha,
        "external_side_effects": "DENY",
        "production_traffic_changed": False,
    }


def evaluate_execution_integrity(
    execution_id,
    *,
    actor: str = "execution-integrity-gate",
) -> dict[str, Any]:
    _require_step1_disabled()
    clean_actor = (actor or "execution-integrity-gate").strip()
    if not clean_actor:
        clean_actor = "execution-integrity-gate"

    with engine.begin() as db:
        row = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """), {"id": execution_id}).mappings().one_or_none()
        if row is None:
            raise LookupError("Production release execution not found")
        return _execution_integrity_locked(
            db,
            dict(row),
            actor=clean_actor,
        )

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
        executor_adapter = _configured_execution_adapter()
        material = _execution_material(
            row,
            bundle_sha256=bundle_sha,
            executor_adapter=executor_adapter,
        )
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
            :executor_adapter,:target_provider,:target_environment,
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
                "executor_adapter": executor_adapter,
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

    gate = evaluate_execution_integrity(
        execution_id,
        actor=clean_actor + ":integrity",
    )
    if gate["allowed"] is not True:
        raise RuntimeError(
            "Execution Integrity Gate blocked: "
            + ", ".join(gate["blocking_reasons"])
            + f" [integrity_check_id={gate['integrity_check_id']}]"
        )

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



def _candidate_result_sha(candidate: Any) -> str:
    supplied = str(
        (candidate.metadata or {}).get("provider_result_sha256") or ""
    ).strip().lower()
    if len(supplied) == 64:
        return supplied
    return _sha256({
        "deployment_id": candidate.deployment_id,
        "url": candidate.url,
        "state": candidate.state,
        "metadata": candidate.metadata,
    })


def _persist_ready_candidate(
    db,
    *,
    execution_id,
    actor: str,
    candidate: Any,
    previous_status: str,
    prepare_outcome: str,
    reconciled: bool,
) -> dict[str, Any]:
    provider_result_sha = _candidate_result_sha(candidate)
    prepared = db.execute(text("""
      UPDATE production_release_executions
      SET execution_status='READY_FOR_PROMOTION',
          candidate_vercel_deployment_id=:deployment_id,
          candidate_vercel_url=:url,
          prepare_provider_state=:provider_state,
          prepare_outcome=:prepare_outcome,
          prepare_provider_result_sha256=:provider_result_sha,
          prepare_reconciled_at=CASE WHEN :reconciled THEN now()
                                     ELSE prepare_reconciled_at END,
          prepared_at=COALESCE(prepared_at,now()),
          production_execution_enabled=false,
          automatic_execution=false,
          automatic_promotion=false
      WHERE id=CAST(:id AS uuid)
      RETURNING *
    """), {
        "id": execution_id,
        "deployment_id": candidate.deployment_id,
        "url": candidate.url,
        "provider_state": candidate.state,
        "prepare_outcome": prepare_outcome,
        "provider_result_sha": provider_result_sha,
        "reconciled": reconciled,
    }).mappings().one()

    _event(
        db,
        execution_id,
        event_type="CANDIDATE_CREATED",
        actor=actor,
        previous_status=previous_status,
        next_status=previous_status,
        details={
            "adapter": "VERCEL_CONTROLLED_EXECUTOR",
            "deployment_id": candidate.deployment_id,
            "provider_write_performed": not reconciled,
            "reconciled": reconciled,
            "auto_assign_custom_domains": False,
            "production_traffic_changed": False,
        },
        provider_result_sha256=provider_result_sha,
    )
    _event(
        db,
        execution_id,
        event_type="CANDIDATE_READY",
        actor=actor,
        previous_status=previous_status,
        next_status="READY_FOR_PROMOTION",
        details={
            "adapter": "VERCEL_CONTROLLED_EXECUTOR",
            "deployment_id": candidate.deployment_id,
            "state": candidate.state,
            "reconciled": reconciled,
            "production_traffic_changed": False,
        },
        provider_result_sha256=provider_result_sha,
    )
    _event(
        db,
        execution_id,
        event_type="CANDIDATE_VERIFIED",
        actor=actor,
        previous_status="READY_FOR_PROMOTION",
        next_status="READY_FOR_PROMOTION",
        details={
            "adapter": "VERCEL_CONTROLLED_EXECUTOR",
            "target_project_id": prepared["target_project_id"],
            "target_team_id": prepared["target_team_id"],
            "auto_assign_custom_domains": False,
            "alias_assigned": False,
            "reconciled": reconciled,
            "production_traffic_changed": False,
        },
        provider_result_sha256=provider_result_sha,
    )
    return dict(prepared)


def prepare_vercel_candidate(
    execution_id,
    *,
    actor: str = "vercel-controlled-preparer",
    adapter: VercelControlledExecutionAdapter | None = None,
) -> dict[str, Any]:
    _require_step1_disabled()
    clean_actor = (actor or "vercel-controlled-preparer").strip()
    if not clean_actor:
        clean_actor = "vercel-controlled-preparer"

    gate = evaluate_execution_integrity(
        execution_id,
        actor=clean_actor + ":integrity",
    )
    if gate["allowed"] is not True:
        raise RuntimeError(
            "Execution Integrity Gate blocked Vercel PREPARE: "
            + ", ".join(gate["blocking_reasons"])
            + f" [integrity_check_id={gate['integrity_check_id']}]"
        )

    with engine.connect() as db:
        source = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
        """), {"id": execution_id}).mappings().one_or_none()
    if source is None:
        raise LookupError("Production release execution not found")
    snapshot = dict(source)
    if snapshot["executor_adapter"] != "VERCEL_CONTROLLED_EXECUTOR":
        raise RuntimeError(
            "Vercel PREPARE requires VERCEL_CONTROLLED_EXECUTOR snapshot"
        )
    if snapshot["execution_status"] == "READY_FOR_PROMOTION":
        return snapshot
    if snapshot["execution_status"] in {"PREPARING", "PREPARE_UNKNOWN"}:
        raise RuntimeError(
            "Vercel PREPARE has already consumed its one provider write; "
            "use reconciliation"
        )
    if snapshot["execution_status"] != "SNAPSHOT_CREATED":
        raise RuntimeError(
            "Vercel PREPARE requires SNAPSHOT_CREATED execution"
        )
    if int(snapshot.get("prepare_write_count") or 0) != 0:
        raise RuntimeError(
            "Vercel PREPARE provider write budget is already consumed"
        )

    provider = adapter or get_production_execution_adapter(
        "VERCEL_CONTROLLED_EXECUTOR"
    )
    if not isinstance(provider, VercelControlledExecutionAdapter) and adapter is None:
        raise RuntimeError("Vercel controlled adapter is unavailable")
    request = provider.build_prepare_request(snapshot)

    with engine.begin() as db:
        locked = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """), {"id": execution_id}).mappings().one_or_none()
        if locked is None:
            raise LookupError("Production release execution not found")
        if locked["execution_sha256"] != snapshot["execution_sha256"]:
            raise RuntimeError("Execution changed before Vercel PREPARE")
        if locked["execution_status"] != "SNAPSHOT_CREATED":
            raise RuntimeError(
                "Execution state changed before Vercel PREPARE"
            )
        if int(locked["prepare_write_count"] or 0) != 0:
            raise RuntimeError(
                "Vercel PREPARE provider write budget is already consumed"
            )

        db.execute(text("""
          UPDATE production_release_executions
          SET execution_status='PREPARING',
              prepare_request_sha256=:request_sha,
              prepare_provider_deployment_id=:deployment_id,
              prepare_provider_state='REQUESTED',
              prepare_outcome='REQUESTED',
              prepare_write_count=1,
              prepare_attempted_at=now()
          WHERE id=CAST(:id AS uuid)
        """), {
            "id": execution_id,
            "request_sha": request.request_sha256,
            "deployment_id": request.deployment_id,
        })
        _event(
            db,
            execution_id,
            event_type="PREPARE_REQUESTED",
            actor=clean_actor,
            previous_status="SNAPSHOT_CREATED",
            next_status="PREPARING",
            details={
                "adapter": "VERCEL_CONTROLLED_EXECUTOR",
                "deployment_id": request.deployment_id,
                "prepare_request_sha256": request.request_sha256,
                "provider_write_budget": 1,
                "provider_write_attempted": True,
                "auto_assign_custom_domains": False,
                "production_traffic_changed": False,
            },
        )

    try:
        candidate = provider.send_prepare(request)
    except ProviderWriteOutcomeUnknown as exc:
        with engine.begin() as db:
            current = db.execute(text("""
              SELECT execution_status
              FROM production_release_executions
              WHERE id=CAST(:id AS uuid)
              FOR UPDATE
            """), {"id": execution_id}).mappings().one()
            if current["execution_status"] != "PREPARING":
                raise RuntimeError(
                    "Execution state changed during ambiguous Vercel PREPARE"
                )
            result = db.execute(text("""
              UPDATE production_release_executions
              SET execution_status='PREPARE_UNKNOWN',
                  prepare_provider_state='UNKNOWN',
                  prepare_outcome='AMBIGUOUS',
                  prepare_last_error_type=:error_type,
                  prepare_last_error_sha256=:error_sha
              WHERE id=CAST(:id AS uuid)
              RETURNING *
            """), {
                "id": execution_id,
                "error_type": exc.error_type[:200],
                "error_sha": exc.evidence_sha256,
            }).mappings().one()
            _event(
                db,
                execution_id,
                event_type="RECONCILIATION_REQUIRED",
                actor=clean_actor,
                previous_status="PREPARING",
                next_status="PREPARE_UNKNOWN",
                details={
                    "adapter": "VERCEL_CONTROLLED_EXECUTOR",
                    "deployment_id": exc.deployment_id,
                    "error_type": exc.error_type,
                    "provider_write_count": 1,
                    "automatic_replay": False,
                    "production_traffic_changed": False,
                },
            )
        output = dict(result)
        output["reconciliation_required"] = True
        return output
    except ProviderPrepareRejected as exc:
        with engine.begin() as db:
            result = db.execute(text("""
              UPDATE production_release_executions
              SET execution_status='PREPARE_FAILED',
                  prepare_provider_state='REJECTED',
                  prepare_outcome='REJECTED',
                  prepare_provider_result_sha256=:result_sha,
                  prepare_last_error_type=:error_type,
                  prepare_last_error_sha256=:error_sha
              WHERE id=CAST(:id AS uuid)
                AND execution_status='PREPARING'
              RETURNING *
            """), {
                "id": execution_id,
                "result_sha": exc.evidence_sha256,
                "error_type": f"HTTP_{exc.status_code}",
                "error_sha": exc.evidence_sha256,
            }).mappings().one()
            _event(
                db,
                execution_id,
                event_type="FAILURE",
                actor=clean_actor,
                previous_status="PREPARING",
                next_status="PREPARE_FAILED",
                details={
                    "adapter": "VERCEL_CONTROLLED_EXECUTOR",
                    "error_type": f"HTTP_{exc.status_code}",
                    "provider_write_count": 1,
                    "automatic_replay": False,
                    "production_traffic_changed": False,
                },
                provider_result_sha256=exc.evidence_sha256,
            )
        raise RuntimeError(
            f"Vercel PREPARE rejected with HTTP {exc.status_code}"
        ) from exc
    except Exception as exc:
        error_sha = _sha256({
            "error_type": type(exc).__name__,
            "message": str(exc)[:2000],
        })
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
                  SET execution_status='PREPARE_FAILED',
                      prepare_provider_state='ERROR',
                      prepare_outcome='REJECTED',
                      prepare_last_error_type=:error_type,
                      prepare_last_error_sha256=:error_sha
                  WHERE id=CAST(:id AS uuid)
                """), {
                    "id": execution_id,
                    "error_type": type(exc).__name__[:200],
                    "error_sha": error_sha,
                })
                _event(
                    db,
                    execution_id,
                    event_type="FAILURE",
                    actor=clean_actor,
                    previous_status="PREPARING",
                    next_status="PREPARE_FAILED",
                    details={
                        "adapter": "VERCEL_CONTROLLED_EXECUTOR",
                        "error_type": type(exc).__name__,
                        "provider_write_count": 1,
                        "automatic_replay": False,
                        "production_traffic_changed": False,
                    },
                )
        raise

    provider_result_sha = _candidate_result_sha(candidate)
    state = candidate.state.upper()
    with engine.begin() as db:
        current = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """), {"id": execution_id}).mappings().one()
        if current["execution_status"] != "PREPARING":
            raise RuntimeError(
                "Execution state changed during Vercel PREPARE"
            )
        if (
            candidate.deployment_id
            != current["prepare_provider_deployment_id"]
        ):
            raise RuntimeError(
                "Vercel PREPARE returned unexpected deployment ID"
            )

        if state == "READY":
            return _persist_ready_candidate(
                db,
                execution_id=execution_id,
                actor=clean_actor,
                candidate=candidate,
                previous_status="PREPARING",
                prepare_outcome="ACCEPTED",
                reconciled=False,
            )

        if state in {"ERROR", "CANCELED", "CANCELLED"}:
            result = db.execute(text("""
              UPDATE production_release_executions
              SET execution_status='PREPARE_FAILED',
                  candidate_vercel_deployment_id=:deployment_id,
                  candidate_vercel_url=:url,
                  prepare_provider_state=:state,
                  prepare_outcome='REJECTED',
                  prepare_provider_result_sha256=:result_sha
              WHERE id=CAST(:id AS uuid)
              RETURNING *
            """), {
                "id": execution_id,
                "deployment_id": candidate.deployment_id,
                "url": candidate.url,
                "state": state,
                "result_sha": provider_result_sha,
            }).mappings().one()
            _event(
                db,
                execution_id,
                event_type="FAILURE",
                actor=clean_actor,
                previous_status="PREPARING",
                next_status="PREPARE_FAILED",
                details={
                    "adapter": "VERCEL_CONTROLLED_EXECUTOR",
                    "deployment_id": candidate.deployment_id,
                    "provider_state": state,
                    "provider_write_performed": True,
                    "production_traffic_changed": False,
                },
                provider_result_sha256=provider_result_sha,
            )
            return dict(result)

        result = db.execute(text("""
          UPDATE production_release_executions
          SET candidate_vercel_deployment_id=:deployment_id,
              candidate_vercel_url=:url,
              prepare_provider_state=:state,
              prepare_outcome='ACCEPTED',
              prepare_provider_result_sha256=:result_sha
          WHERE id=CAST(:id AS uuid)
          RETURNING *
        """), {
            "id": execution_id,
            "deployment_id": candidate.deployment_id,
            "url": candidate.url,
            "state": state,
            "result_sha": provider_result_sha,
        }).mappings().one()
        _event(
            db,
            execution_id,
            event_type="CANDIDATE_CREATED",
            actor=clean_actor,
            previous_status="PREPARING",
            next_status="PREPARING",
            details={
                "adapter": "VERCEL_CONTROLLED_EXECUTOR",
                "deployment_id": candidate.deployment_id,
                "provider_state": state,
                "provider_write_performed": True,
                "auto_assign_custom_domains": False,
                "production_traffic_changed": False,
            },
            provider_result_sha256=provider_result_sha,
        )
        return dict(result)


def reconcile_vercel_prepare(
    execution_id,
    *,
    actor: str = "vercel-prepare-reconciler",
    adapter: VercelControlledExecutionAdapter | None = None,
) -> dict[str, Any]:
    _require_step1_disabled()
    clean_actor = (actor or "vercel-prepare-reconciler").strip()
    if not clean_actor:
        clean_actor = "vercel-prepare-reconciler"

    gate = evaluate_execution_integrity(
        execution_id,
        actor=clean_actor + ":integrity",
    )
    if gate["allowed"] is not True:
        raise RuntimeError(
            "Execution Integrity Gate blocked PREPARE reconciliation: "
            + ", ".join(gate["blocking_reasons"])
            + f" [integrity_check_id={gate['integrity_check_id']}]"
        )

    with engine.connect() as db:
        row = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
        """), {"id": execution_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Production release execution not found")
    snapshot = dict(row)
    if snapshot["executor_adapter"] != "VERCEL_CONTROLLED_EXECUTOR":
        raise RuntimeError(
            "Vercel PREPARE reconciliation requires Vercel adapter"
        )
    if snapshot["execution_status"] == "READY_FOR_PROMOTION":
        return snapshot
    if snapshot["execution_status"] not in {
        "PREPARING",
        "PREPARE_UNKNOWN",
    }:
        raise RuntimeError(
            "Vercel PREPARE reconciliation requires pending PREPARE"
        )
    if int(snapshot.get("prepare_write_count") or 0) != 1:
        raise RuntimeError(
            "Vercel PREPARE reconciliation requires consumed write budget"
        )
    deployment_id = str(
        snapshot.get("prepare_provider_deployment_id") or ""
    ).strip()
    if not deployment_id:
        raise RuntimeError(
            "Vercel PREPARE reconciliation deployment ID is unavailable"
        )

    provider = adapter or get_production_execution_adapter(
        "VERCEL_CONTROLLED_EXECUTOR"
    )
    candidate = provider.read_candidate(snapshot, deployment_id)

    with engine.begin() as db:
        current = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """), {"id": execution_id}).mappings().one()
        previous_status = current["execution_status"]
        if previous_status not in {"PREPARING", "PREPARE_UNKNOWN"}:
            raise RuntimeError(
                "Execution state changed during PREPARE reconciliation"
            )
        if int(current["prepare_write_count"] or 0) != 1:
            raise RuntimeError(
                "PREPARE reconciliation detected invalid write count"
            )

        if candidate is None:
            next_status = (
                "PREPARE_UNKNOWN"
                if previous_status == "PREPARING"
                else previous_status
            )
            result = db.execute(text("""
              UPDATE production_release_executions
              SET execution_status=:next_status,
                  prepare_provider_state='NOT_FOUND',
                  prepare_outcome='RECONCILED_PENDING',
                  prepare_reconciled_at=now()
              WHERE id=CAST(:id AS uuid)
              RETURNING *
            """), {
                "id": execution_id,
                "next_status": next_status,
            }).mappings().one()
            _event(
                db,
                execution_id,
                event_type="RECONCILIATION_REQUIRED",
                actor=clean_actor,
                previous_status=previous_status,
                next_status=next_status,
                details={
                    "adapter": "VERCEL_CONTROLLED_EXECUTOR",
                    "deployment_id": deployment_id,
                    "provider_state": "NOT_FOUND",
                    "provider_write_count": 1,
                    "automatic_replay": False,
                    "reconciliation_read_only": True,
                    "production_traffic_changed": False,
                },
            )
            output = dict(result)
            output["reconciliation_required"] = True
            return output

        state = candidate.state.upper()
        provider_result_sha = _candidate_result_sha(candidate)
        if state == "READY":
            return _persist_ready_candidate(
                db,
                execution_id=execution_id,
                actor=clean_actor,
                candidate=candidate,
                previous_status=previous_status,
                prepare_outcome="RECONCILED_READY",
                reconciled=True,
            )

        if state in {"ERROR", "CANCELED", "CANCELLED"}:
            result = db.execute(text("""
              UPDATE production_release_executions
              SET execution_status='PREPARE_FAILED',
                  candidate_vercel_deployment_id=:deployment_id,
                  candidate_vercel_url=:url,
                  prepare_provider_state=:state,
                  prepare_outcome='RECONCILED_FAILED',
                  prepare_provider_result_sha256=:result_sha,
                  prepare_reconciled_at=now()
              WHERE id=CAST(:id AS uuid)
              RETURNING *
            """), {
                "id": execution_id,
                "deployment_id": candidate.deployment_id,
                "url": candidate.url,
                "state": state,
                "result_sha": provider_result_sha,
            }).mappings().one()
            _event(
                db,
                execution_id,
                event_type="FAILURE",
                actor=clean_actor,
                previous_status=previous_status,
                next_status="PREPARE_FAILED",
                details={
                    "adapter": "VERCEL_CONTROLLED_EXECUTOR",
                    "deployment_id": candidate.deployment_id,
                    "provider_state": state,
                    "reconciliation_read_only": True,
                    "provider_write_count": 1,
                    "production_traffic_changed": False,
                },
                provider_result_sha256=provider_result_sha,
            )
            return dict(result)

        result = db.execute(text("""
          UPDATE production_release_executions
          SET candidate_vercel_deployment_id=:deployment_id,
              candidate_vercel_url=:url,
              prepare_provider_state=:state,
              prepare_outcome='RECONCILED_PENDING',
              prepare_provider_result_sha256=:result_sha,
              prepare_reconciled_at=now()
          WHERE id=CAST(:id AS uuid)
          RETURNING *
        """), {
            "id": execution_id,
            "deployment_id": candidate.deployment_id,
            "url": candidate.url,
            "state": state,
            "result_sha": provider_result_sha,
        }).mappings().one()
        _event(
            db,
            execution_id,
            event_type="RECONCILIATION_REQUIRED",
            actor=clean_actor,
            previous_status=previous_status,
            next_status=previous_status,
            details={
                "adapter": "VERCEL_CONTROLLED_EXECUTOR",
                "deployment_id": candidate.deployment_id,
                "provider_state": state,
                "reconciliation_read_only": True,
                "provider_write_count": 1,
                "automatic_replay": False,
                "production_traffic_changed": False,
            },
            provider_result_sha256=provider_result_sha,
        )
        output = dict(result)
        output["reconciliation_required"] = True
        return output


def prepare_controlled_candidate(
    execution_id,
    *,
    actor: str = "controlled-production-preparer",
) -> dict[str, Any]:
    with engine.connect() as db:
        row = db.execute(text("""
          SELECT executor_adapter
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
        """), {"id": execution_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Production release execution not found")

    adapter = str(row["executor_adapter"] or "").strip().upper()
    if adapter == "MOCK":
        return prepare_mock_candidate(execution_id, actor=actor)
    if adapter == "VERCEL_CONTROLLED_EXECUTOR":
        return prepare_vercel_candidate(execution_id, actor=actor)
    raise RuntimeError("Unsupported execution adapter")


def reconcile_controlled_prepare(
    execution_id,
    *,
    actor: str = "controlled-production-reconciler",
) -> dict[str, Any]:
    with engine.connect() as db:
        row = db.execute(text("""
          SELECT executor_adapter
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
        """), {"id": execution_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Production release execution not found")
    if str(row["executor_adapter"] or "").strip().upper() != (
        "VERCEL_CONTROLLED_EXECUTOR"
    ):
        raise RuntimeError(
            "PREPARE reconciliation is only available for Vercel adapter"
        )
    return reconcile_vercel_prepare(execution_id, actor=actor)


def _production_execution_decision_material(
    row: dict[str, Any],
    *,
    decision: str,
    reason: str,
    actor: str,
    candidate_verified_event_id: str,
    candidate_provider_result_sha256: str,
    integrity_check_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": "production-execution-decision-v1",
        "execution_id": str(row["id"]),
        "execution_sha256": row["execution_sha256"],
        "deployment_plan_id": str(row["deployment_plan_id"]),
        "plan_sha256": row["plan_sha256"],
        "decision": decision,
        "reason": reason,
        "actor": actor,
        "candidate_vercel_deployment_id": (
            row["candidate_vercel_deployment_id"]
        ),
        "candidate_vercel_url": row["candidate_vercel_url"],
        "target_project_id": row["target_project_id"],
        "target_team_id": row["target_team_id"],
        "candidate_verified_event_id": candidate_verified_event_id,
        "candidate_provider_result_sha256": (
            candidate_provider_result_sha256
        ),
        "integrity_check_id": integrity_check_id,
        "provider_write_performed": False,
        "production_traffic_changed": False,
    }


def decide_production_execution(
    execution_id,
    *,
    decision: str,
    reason: str,
    actor: str,
    execution_sha256: str,
) -> dict[str, Any]:
    _require_step1_disabled()

    normalized = (decision or "").strip().upper()
    clean_reason = (reason or "").strip()
    clean_actor = (
        (actor or "human-production-execution-api").strip()
        or "human-production-execution-api"
    )
    supplied_sha = (execution_sha256 or "").strip().lower()

    if normalized not in {"PROMOTE", "ABORT"}:
        raise ValueError("decision must be PROMOTE or ABORT")
    if len(clean_reason) < 3:
        raise ValueError("reason must contain at least 3 characters")
    if len(supplied_sha) != 64:
        raise ValueError(
            "execution_sha256 must be a 64-character SHA-256"
        )

    with engine.begin() as db:
        row = db.execute(text("""
          SELECT *
          FROM production_release_executions
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """), {"id": execution_id}).mappings().one_or_none()
        if row is None:
            raise LookupError("Production release execution not found")
        row = dict(row)

        if supplied_sha != str(row["execution_sha256"]).lower():
            raise RuntimeError("Production execution SHA-256 does not match")

        existing = db.execute(text("""
          SELECT *
          FROM production_release_execution_decisions
          WHERE execution_id=:id
          FOR UPDATE
        """), {"id": row["id"]}).mappings().one_or_none()
        if existing is not None:
            if (
                existing["decision"] != normalized
                or existing["execution_sha256"] != row["execution_sha256"]
            ):
                raise RuntimeError(
                    "Production execution decision is already terminal"
                )
            result = dict(existing)
            result.update({
                "idempotent": True,
                "execution_status": row["execution_status"],
                "promotion_authorized": normalized == "PROMOTE",
                "provider_write_performed": False,
                "production_traffic_changed": False,
            })
            return result

        if row["execution_status"] != "READY_FOR_PROMOTION":
            raise RuntimeError(
                "Human production execution decision requires READY_FOR_PROMOTION"
            )
        if (
            not row["candidate_vercel_deployment_id"]
            or not row["candidate_vercel_url"]
        ):
            raise RuntimeError(
                "Human production execution decision requires persisted candidate"
            )

        verified_event = db.execute(text("""
          SELECT id,provider_result_sha256
          FROM production_release_execution_events
          WHERE execution_id=:id
            AND event_type='CANDIDATE_VERIFIED'
          ORDER BY sequence_no DESC
          LIMIT 1
        """), {"id": row["id"]}).mappings().one_or_none()
        if (
            verified_event is None
            or not verified_event["provider_result_sha256"]
            or len(verified_event["provider_result_sha256"]) != 64
        ):
            raise RuntimeError(
                "Human production execution decision requires "
                "CANDIDATE_VERIFIED evidence"
            )

        blocked_error: str | None = None
        if normalized == "PROMOTE":
            gate = _execution_integrity_locked(
                db,
                row,
                actor=clean_actor + ":pre-promotion-integrity",
            )
            integrity_check_id = gate["integrity_check_id"]
            if gate["allowed"] is not True:
                blocked_error = (
                    "Execution Integrity Gate blocked PROMOTE: "
                    + ", ".join(gate["blocking_reasons"])
                    + f" [integrity_check_id={gate['integrity_check_id']}]"
                )
        else:
            latest_check = db.execute(text("""
              SELECT id
              FROM production_release_execution_integrity_checks
              WHERE execution_id=:id
              ORDER BY checked_at DESC,id DESC
              LIMIT 1
            """), {"id": row["id"]}).mappings().one_or_none()
            if latest_check is None:
                raise RuntimeError(
                    "ABORT requires prior Execution Integrity Gate evidence"
                )
            integrity_check_id = str(latest_check["id"])

        if blocked_error is not None:
            saved = None
            next_status = row["execution_status"]
        else:
            material = _production_execution_decision_material(
                row,
                decision=normalized,
                reason=clean_reason,
                actor=clean_actor,
                candidate_verified_event_id=str(verified_event["id"]),
                candidate_provider_result_sha256=(
                    verified_event["provider_result_sha256"]
                ),
                integrity_check_id=integrity_check_id,
            )
            decision_sha = _sha256(material)

            saved = db.execute(text("""
          INSERT INTO production_release_execution_decisions(
            execution_id,decision,reason,actor,execution_sha256,
            deployment_plan_id,plan_sha256,
            candidate_vercel_deployment_id,candidate_vercel_url,
            target_project_id,target_team_id,
            candidate_verified_event_id,candidate_provider_result_sha256,
            integrity_check_id,decision_sha256,
            provider_write_performed,production_traffic_changed)
          VALUES(
            :execution_id,:decision,:reason,:actor,:execution_sha256,
            :deployment_plan_id,:plan_sha256,
            :candidate_vercel_deployment_id,:candidate_vercel_url,
            :target_project_id,:target_team_id,
            CAST(:candidate_verified_event_id AS uuid),
            :candidate_provider_result_sha256,
            CAST(:integrity_check_id AS uuid),:decision_sha256,
            false,false)
          RETURNING *
            """), {
                **material,
                "decision_sha256": decision_sha,
            }).mappings().one()

            next_status = row["execution_status"]
            if normalized == "ABORT":
                db.execute(text("""
                  UPDATE production_release_executions
                  SET execution_status='ABORTED'
                  WHERE id=:id
                """), {"id": row["id"]})
                next_status = "ABORTED"

    if blocked_error is not None:
        raise RuntimeError(blocked_error)

    result = dict(saved)
    result.update({
        "idempotent": False,
        "execution_status": next_status,
        "promotion_authorized": normalized == "PROMOTE",
        "provider_write_performed": False,
        "production_traffic_changed": False,
    })
    return result

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
              SELECT id,decision,reason,actor,execution_sha256,
                     deployment_plan_id,plan_sha256,
                     candidate_vercel_deployment_id,candidate_vercel_url,
                     target_project_id,target_team_id,
                     candidate_verified_event_id,
                     candidate_provider_result_sha256,integrity_check_id,
                     decision_sha256,provider_write_performed,
                     production_traffic_changed,decided_at
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
        integrity_checks = [
            dict(item)
            for item in db.execute(text("""
              SELECT id,check_status,actor,execution_sha256,blocking_reasons,
                     current_manifest_root_sha256,current_chain_head_sha256,
                     stored_chain_head_is_ancestor,registry_chain_extended,
                     external_side_effects,production_traffic_changed,checked_at
              FROM production_release_execution_integrity_checks
              WHERE execution_id=:id
              ORDER BY checked_at,id
            """), {"id": row["id"]}).mappings().all()
        ]

    result = dict(row)
    result["decisions"] = decisions
    result["events"] = events
    result["integrity_checks"] = integrity_checks
    return result
