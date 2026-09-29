import hashlib
import json
import re
from typing import Any

from sqlalchemy import text

from app.db import engine
from app.release_integrity_gate import evaluate_release_integrity

_TARGET_ID = re.compile(r"^[A-Za-z0-9_.-]{3,160}$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _clean_target(value: str, *, field: str) -> str:
    result = (value or "").strip()
    if not _TARGET_ID.fullmatch(result):
        raise ValueError(
            f"{field} must contain 3-160 letters, digits, dot, dash, or underscore"
        )
    return result


def _optional_target(value: str | None, *, field: str) -> str | None:
    if value is None or not value.strip():
        return None
    return _clean_target(value, field=field)


def _plan_material(
    *,
    release_candidate_id: str,
    release_decision_id: str,
    review_package_id: str,
    target_project_id: str,
    target_team_id: str | None,
    review_package_sha256: str,
    source_tree_sha256: str,
    integrity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "deployment-plan-v1",
        "release_candidate_id": release_candidate_id,
        "release_decision_id": release_decision_id,
        "review_package_id": review_package_id,
        "target_provider": "VERCEL",
        "target_environment": "production",
        "target_project_id": target_project_id,
        "target_team_id": target_team_id,
        "review_package_sha256": review_package_sha256,
        "source_tree_sha256": source_tree_sha256,
        "acceptance_provenance_tree_sha256": (
            integrity["acceptance_provenance_tree_sha256"]
        ),
        "live_acceptance_audit_id": integrity["audit_id"],
        "audit_evidence_sha256": integrity["audit_evidence_sha256"],
        "audit_chain_sha256": integrity["audit_chain_sha256"],
        "manifest_root_sha256": integrity["manifest_root_sha256"],
        "chain_head_sha256": integrity["chain_head_sha256"],
        "source_commit": integrity["source_commit"],
        "deployment_source_commit": integrity["deployment_source_commit"],
        "source_vercel_deployment_id": integrity["vercel_deployment_id"],
        "execution_enabled": False,
    }


def _require_integrity_fields(integrity: dict[str, Any]) -> None:
    if integrity.get("allowed") is not True:
        reasons = integrity.get("blocking_reasons") or ["integrity_not_verified"]
        raise RuntimeError(
            "Deployment plan blocked by Release Integrity Gate: "
            + ", ".join(reasons)
        )
    required = (
        "acceptance_provenance_tree_sha256",
        "audit_id",
        "audit_evidence_sha256",
        "audit_chain_sha256",
        "manifest_root_sha256",
        "chain_head_sha256",
        "source_commit",
        "deployment_source_commit",
        "vercel_deployment_id",
    )
    missing = [name for name in required if not integrity.get(name)]
    if missing:
        raise RuntimeError(
            "Deployment plan provenance is incomplete: " + ", ".join(missing)
        )


def create_deployment_plan(
    candidate_id,
    *,
    target_project_id: str,
    target_team_id: str | None = None,
    actor: str = "human-deployment-api",
) -> dict[str, Any]:
    project_id = _clean_target(target_project_id, field="target_project_id")
    team_id = _optional_target(target_team_id, field="target_team_id")
    clean_actor = (actor or "human-deployment-api").strip() or "human-deployment-api"

    with engine.begin() as db:
        row = db.execute(text("""
          SELECT rc.id,
                 rc.release_status,
                 rc.deployment_enabled,
                 rc.archived_at,
                 rd.id AS release_decision_id,
                 rd.decision AS release_decision,
                 rd.candidate_status AS release_decision_status,
                 rd.review_package_id AS decision_review_package_id,
                 rd.review_package_sha256 AS decision_review_package_sha256,
                 rd.source_tree_sha256 AS decision_source_tree_sha256,
                 rrp.id AS review_package_id,
                 rrp.package_status,
                 rrp.content_snapshot_complete,
                 rrp.package_sha256,
                 rrp.source_tree_sha256,
                 rrp.artifact_manifest
          FROM release_candidates rc
          JOIN LATERAL (
            SELECT *
            FROM release_decisions
            WHERE release_candidate_id=rc.id
              AND decision='APPROVE'
            ORDER BY decided_at DESC,id DESC
            LIMIT 1
          ) rd ON true
          JOIN release_review_packages rrp
            ON rrp.id=rd.review_package_id
           AND rrp.release_candidate_id=rc.id
          WHERE rc.id=CAST(:id AS uuid)
          FOR UPDATE OF rc
        """), {"id": candidate_id}).mappings().one_or_none()

        if row is None:
            raise LookupError("Approved release candidate not found")
        if row["archived_at"] is not None:
            raise RuntimeError("Archived release candidate cannot be deployed")
        if row["release_status"] != "RELEASE_APPROVED":
            raise RuntimeError("Deployment plan requires RELEASE_APPROVED")
        if row["deployment_enabled"]:
            raise RuntimeError("Release candidate deployment must remain disabled")
        if (
            row["release_decision"] != "APPROVE"
            or row["release_decision_status"] != "RELEASE_APPROVED"
        ):
            raise RuntimeError("Deployment plan requires persisted APPROVE decision")
        if (
            row["package_status"] != "GENERATED"
            or row["content_snapshot_complete"] is not True
        ):
            raise RuntimeError(
                "Deployment plan requires complete immutable Review Package"
            )
        if (
            str(row["decision_review_package_id"]) != str(row["review_package_id"])
            or row["decision_review_package_sha256"] != row["package_sha256"]
            or row["decision_source_tree_sha256"] != row["source_tree_sha256"]
        ):
            raise RuntimeError("Release Decision / Review Package binding drifted")

        integrity = evaluate_release_integrity(
            row["source_tree_sha256"],
            list(row["artifact_manifest"] or []),
        )
        _require_integrity_fields(integrity)

        material = _plan_material(
            release_candidate_id=str(row["id"]),
            release_decision_id=str(row["release_decision_id"]),
            review_package_id=str(row["review_package_id"]),
            target_project_id=project_id,
            target_team_id=team_id,
            review_package_sha256=str(row["package_sha256"]),
            source_tree_sha256=str(row["source_tree_sha256"]),
            integrity=integrity,
        )
        plan_sha = _sha256(material)

        existing = db.execute(text("""
          SELECT *
          FROM deployment_plans
          WHERE release_candidate_id=:candidate_id
          FOR UPDATE
        """), {"candidate_id": row["id"]}).mappings().one_or_none()

        if existing is not None:
            if existing["plan_sha256"] != plan_sha:
                raise RuntimeError(
                    "Deployment Plan already exists with a different immutable target or provenance"
                )
            return dict(existing)

        plan = db.execute(text("""
          INSERT INTO deployment_plans(
            release_candidate_id,release_decision_id,review_package_id,
            target_provider,target_environment,target_project_id,target_team_id,
            review_package_sha256,source_tree_sha256,
            acceptance_provenance_tree_sha256,live_acceptance_audit_id,
            audit_evidence_sha256,audit_chain_sha256,manifest_root_sha256,
            chain_head_sha256,source_commit,deployment_source_commit,
            source_vercel_deployment_id,plan_sha256,execution_enabled,created_by)
          VALUES(
            :release_candidate_id,:release_decision_id,:review_package_id,
            'VERCEL','production',:target_project_id,:target_team_id,
            :review_package_sha256,:source_tree_sha256,
            :acceptance_provenance_tree_sha256,:live_acceptance_audit_id,
            :audit_evidence_sha256,:audit_chain_sha256,:manifest_root_sha256,
            :chain_head_sha256,:source_commit,:deployment_source_commit,
            :source_vercel_deployment_id,:plan_sha256,false,:created_by)
          RETURNING *
        """), {
            **material,
            "plan_sha256": plan_sha,
            "created_by": clean_actor[:200],
        }).mappings().one()

    return dict(plan)


def _current_authorization_snapshot(db, plan_id):
    return db.execute(text("""
      SELECT dp.*,
             rc.release_status,
             rc.deployment_enabled AS candidate_deployment_enabled,
             rc.archived_at,
             rd.decision AS release_decision,
             rd.candidate_status AS release_decision_status,
             rd.review_package_id AS decision_review_package_id,
             rd.review_package_sha256 AS decision_review_package_sha256,
             rd.source_tree_sha256 AS decision_source_tree_sha256,
             rrp.package_status,
             rrp.content_snapshot_complete,
             rrp.package_sha256 AS current_package_sha256,
             rrp.source_tree_sha256 AS current_source_tree_sha256,
             rrp.artifact_manifest AS current_artifact_manifest
      FROM deployment_plans dp
      JOIN release_candidates rc ON rc.id=dp.release_candidate_id
      JOIN release_decisions rd ON rd.id=dp.release_decision_id
      JOIN release_review_packages rrp ON rrp.id=dp.review_package_id
      WHERE dp.id=CAST(:id AS uuid)
      FOR UPDATE OF dp,rc
    """), {"id": plan_id}).mappings().one_or_none()


def _authorization_drift_reasons(
    row: dict[str, Any],
    integrity: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if row["release_status"] != "RELEASE_APPROVED":
        reasons.append("release_candidate_not_approved")
    if row["candidate_deployment_enabled"]:
        reasons.append("candidate_deployment_enabled_unexpectedly")
    if row["archived_at"] is not None:
        reasons.append("release_candidate_archived")
    if (
        row["release_decision"] != "APPROVE"
        or row["release_decision_status"] != "RELEASE_APPROVED"
    ):
        reasons.append("release_decision_not_approved")
    if (
        str(row["decision_review_package_id"]) != str(row["review_package_id"])
        or row["decision_review_package_sha256"] != row["review_package_sha256"]
        or row["decision_source_tree_sha256"] != row["source_tree_sha256"]
    ):
        reasons.append("release_decision_binding_drift")
    if (
        row["package_status"] != "GENERATED"
        or row["content_snapshot_complete"] is not True
        or row["current_package_sha256"] != row["review_package_sha256"]
        or row["current_source_tree_sha256"] != row["source_tree_sha256"]
    ):
        reasons.append("review_package_binding_drift")

    if integrity.get("allowed") is not True:
        reasons.extend(
            "integrity_" + item
            for item in (integrity.get("blocking_reasons") or ["not_verified"])
        )

    comparisons = {
        "acceptance_provenance_tree_sha256": integrity.get(
            "acceptance_provenance_tree_sha256"
        ),
        "live_acceptance_audit_id": integrity.get("audit_id"),
        "audit_evidence_sha256": integrity.get("audit_evidence_sha256"),
        "audit_chain_sha256": integrity.get("audit_chain_sha256"),
        "manifest_root_sha256": integrity.get("manifest_root_sha256"),
        "chain_head_sha256": integrity.get("chain_head_sha256"),
        "source_commit": integrity.get("source_commit"),
        "deployment_source_commit": integrity.get("deployment_source_commit"),
        "source_vercel_deployment_id": integrity.get("vercel_deployment_id"),
    }
    for field, current in comparisons.items():
        if row[field] != current:
            reasons.append(field + "_drift")

    return list(dict.fromkeys(reasons))


def decide_deployment_authorization(
    plan_id,
    *,
    decision: str,
    reason: str,
    actor: str,
    plan_sha256: str,
) -> dict[str, Any]:
    normalized = (decision or "").upper().strip()
    clean_reason = (reason or "").strip()
    clean_actor = (actor or "human-deployment-api").strip() or "human-deployment-api"
    supplied_sha = (plan_sha256 or "").strip().lower()

    if normalized not in {"AUTHORIZE", "REJECT"}:
        raise ValueError("decision must be AUTHORIZE or REJECT")
    if len(clean_reason) < 3:
        raise ValueError("reason must contain at least 3 characters")
    if len(supplied_sha) != 64:
        raise ValueError("plan_sha256 must be a 64-character SHA-256")

    blocked_error = None
    block_id = None
    decision_id = None
    final_status = None
    row = None
    integrity = None

    with engine.begin() as db:
        row = _current_authorization_snapshot(db, plan_id)
        if row is None:
            raise LookupError("Deployment Plan not found")
        if row["plan_status"] != "PENDING_AUTHORIZATION":
            raise RuntimeError("Deployment authorization decision is already terminal")
        if row["execution_enabled"]:
            raise RuntimeError("Deployment executor must remain disabled")
        if supplied_sha != str(row["plan_sha256"]).lower():
            raise RuntimeError("Deployment Plan SHA-256 does not match")

        if normalized == "AUTHORIZE":
            integrity = evaluate_release_integrity(
                row["current_source_tree_sha256"],
                list(row["current_artifact_manifest"] or []),
            )
            reasons = _authorization_drift_reasons(dict(row), integrity)
            if reasons:
                message = "Deployment Authorization blocked: " + ", ".join(reasons)
                block_id = db.execute(text("""
                  INSERT INTO deployment_authorization_blocks(
                    deployment_plan_id,actor,reason,blocking_reasons,
                    integrity_status,current_manifest_root_sha256,
                    current_chain_head_sha256)
                  VALUES(
                    :plan_id,:actor,:reason,CAST(:blocking_reasons AS jsonb),
                    :integrity_status,:manifest_root,:chain_head)
                  RETURNING id
                """), {
                    "plan_id": row["id"],
                    "actor": clean_actor[:200],
                    "reason": message[:4000],
                    "blocking_reasons": json.dumps(reasons, ensure_ascii=False),
                    "integrity_status": str(
                        integrity.get("integrity_status") or "UNKNOWN"
                    ),
                    "manifest_root": integrity.get("manifest_root_sha256"),
                    "chain_head": integrity.get("chain_head_sha256"),
                }).scalar_one()
                blocked_error = message
            else:
                final_status = "AUTHORIZED_FOR_DEPLOYMENT"
        else:
            final_status = "DEPLOYMENT_REJECTED"

        if blocked_error is None:
            decision_id = db.execute(text("""
              INSERT INTO deployment_authorization_decisions(
                deployment_plan_id,decision,reason,actor,plan_sha256)
              VALUES(:plan_id,:decision,:reason,:actor,:plan_sha256)
              RETURNING id
            """), {
                "plan_id": row["id"],
                "decision": normalized,
                "reason": clean_reason[:4000],
                "actor": clean_actor[:200],
                "plan_sha256": row["plan_sha256"],
            }).scalar_one()

            if normalized == "AUTHORIZE":
                db.execute(text("""
                  UPDATE deployment_plans
                  SET plan_status='AUTHORIZED_FOR_DEPLOYMENT',
                      authorized_at=now(),
                      execution_enabled=false
                  WHERE id=:id
                """), {"id": row["id"]})
            else:
                db.execute(text("""
                  UPDATE deployment_plans
                  SET plan_status='DEPLOYMENT_REJECTED',
                      rejected_at=now(),
                      execution_enabled=false
                  WHERE id=:id
                """), {"id": row["id"]})

    if blocked_error is not None:
        raise RuntimeError(
            blocked_error + f" [block_event_id={block_id}]"
        )

    return {
        "deployment_plan_id": str(row["id"]),
        "plan_status": final_status,
        "decision_id": str(decision_id),
        "plan_sha256": row["plan_sha256"],
        "target_provider": row["target_provider"],
        "target_environment": row["target_environment"],
        "target_project_id": row["target_project_id"],
        "execution_enabled": False,
        "production_deployment_executed": False,
    }


def get_deployment_plan_for_candidate(candidate_id) -> dict[str, Any] | None:
    with engine.connect() as db:
        plan = db.execute(text("""
          SELECT *
          FROM deployment_plans
          WHERE release_candidate_id=CAST(:id AS uuid)
        """), {"id": candidate_id}).mappings().one_or_none()
        if plan is None:
            return None
        decisions = [
            dict(row)
            for row in db.execute(text("""
              SELECT id,decision,reason,actor,plan_sha256,decided_at
              FROM deployment_authorization_decisions
              WHERE deployment_plan_id=:id
              ORDER BY decided_at DESC,id DESC
            """), {"id": plan["id"]}).mappings().all()
        ]
        blocks = [
            dict(row)
            for row in db.execute(text("""
              SELECT id,actor,reason,blocking_reasons,integrity_status,
                     current_manifest_root_sha256,current_chain_head_sha256,
                     blocked_at
              FROM deployment_authorization_blocks
              WHERE deployment_plan_id=:id
              ORDER BY blocked_at DESC,id DESC
            """), {"id": plan["id"]}).mappings().all()
        ]
    result = dict(plan)
    result["decisions"] = decisions
    result["blocks"] = blocks
    return result


def get_deployment_plan(plan_id) -> dict[str, Any]:
    with engine.connect() as db:
        row = db.execute(text("""
          SELECT *
          FROM deployment_plans
          WHERE id=CAST(:id AS uuid)
        """), {"id": plan_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Deployment Plan not found")
    return dict(row)
