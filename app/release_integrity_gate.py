import hashlib
import json
from typing import Any

from sqlalchemy import text

from app.live_acceptance_registry import (
    list_live_acceptance_audits,
    live_acceptance_integrity_manifest,
)



def acceptance_provenance_tree_sha256(
    artifact_manifest: list[dict[str, Any]] | None,
) -> str | None:
    if not artifact_manifest:
        return None
    rows = []
    for item in sorted(
        artifact_manifest,
        key=lambda value: str(value.get("relative_path") or ""),
    ):
        relative_path = str(item.get("relative_path") or "")
        sha256 = str(item.get("sha256") or "").lower()
        byte_size = item.get("byte_size")
        if (
            not relative_path
            or len(sha256) != 64
            or byte_size is None
        ):
            return None
        rows.append({
            "relative_path": relative_path,
            "sha256": sha256,
            "byte_size": int(byte_size),
        })
    encoded = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def evaluate_release_integrity(
    source_tree_sha256: str | None,
    artifact_manifest: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    review_tree = (source_tree_sha256 or "").strip().lower()
    acceptance_tree = acceptance_provenance_tree_sha256(artifact_manifest)
    expected = (acceptance_tree or review_tree).strip().lower()
    manifest = live_acceptance_integrity_manifest()
    records = list_live_acceptance_audits(limit=500)

    reasons: list[str] = []
    if not expected:
        reasons.append("release_source_tree_sha256_missing")

    if manifest.get("manifest_present") is not True:
        reasons.append("integrity_manifest_missing")
    if manifest.get("manifest_root_valid") is not True:
        reasons.append("manifest_root_invalid")
    if manifest.get("chain_valid") is not True:
        reasons.append("integrity_chain_invalid")
    if manifest.get("status") != "VERIFIED":
        reasons.append(
            "integrity_manifest_status_"
            + str(manifest.get("status") or "UNKNOWN").lower()
        )

    matches = [
        record
        for record in records
        if expected
        and str(record.get("source_tree_sha256") or "").strip().lower() == expected
    ]

    audit: dict[str, Any] | None = None
    integrity_status = "ORPHANED"

    if len(matches) == 0:
        reasons.append("matching_live_acceptance_audit_not_found")
    elif len(matches) > 1:
        reasons.append("multiple_live_acceptance_audits_for_source_tree")
        integrity_status = "TAMPERED"
    else:
        audit = matches[0]
        integrity_status = str(
            audit.get("integrity_status") or "ORPHANED"
        ).upper()
        if audit.get("acceptance_status") != "PASSED":
            reasons.append("matching_live_acceptance_audit_not_passed")
        if audit.get("gateway_mode") != "PROXY":
            reasons.append("matching_live_acceptance_audit_gateway_not_proxy")
        if audit.get("live_model_verified") is not True:
            reasons.append(
                "matching_live_acceptance_audit_live_model_not_verified"
            )
        if audit.get("budget_status") != "WITHIN_BUDGET":
            reasons.append("matching_live_acceptance_audit_budget_not_within_budget")
        if audit.get("tests_passed") is not True:
            reasons.append("matching_live_acceptance_audit_tests_not_passed")
        if audit.get("external_side_effects") != "DENY":
            reasons.append(
                "matching_live_acceptance_audit_side_effects_not_denied"
            )
        if audit.get("release_approved") is True:
            reasons.append(
                "matching_live_acceptance_audit_release_side_effect_detected"
            )
        if audit.get("integrity_status") != "VERIFIED":
            reasons.append(
                "matching_audit_integrity_"
                + str(audit.get("integrity_status") or "ORPHANED").lower()
            )
        source_commit = str(audit.get("source_commit") or "").strip().lower()
        deployment_commit = str(
            audit.get("deployment_source_commit") or ""
        ).strip().lower()
        if not source_commit or source_commit != deployment_commit:
            reasons.append("deployment_provenance_mismatch")
            integrity_status = "TAMPERED"
        if not audit.get("chain_sha256"):
            reasons.append("matching_audit_chain_sha256_missing")
        if not audit.get("evidence_sha256"):
            reasons.append("matching_audit_evidence_sha256_missing")

    if manifest.get("status") == "TAMPERED":
        integrity_status = "TAMPERED"
    elif integrity_status != "TAMPERED" and (
        manifest.get("status") == "ORPHANED" or audit is None
    ):
        integrity_status = "ORPHANED"

    allowed = len(reasons) == 0

    return {
        "allowed": allowed,
        "integrity_status": "VERIFIED" if allowed else integrity_status,
        "blocking_reasons": reasons,
        "source_tree_sha256": review_tree or None,
        "acceptance_provenance_tree_sha256": acceptance_tree,
        "matched_audit_source_tree_sha256": expected or None,
        "audit_id": audit.get("audit_id") if audit else None,
        "audit_acceptance_status": audit.get("acceptance_status") if audit else None,
        "audit_integrity_status": audit.get("integrity_status") if audit else None,
        "audit_live_model_verified": (
            audit.get("live_model_verified") if audit else None
        ),
        "audit_budget_status": audit.get("budget_status") if audit else None,
        "audit_tests_passed": audit.get("tests_passed") if audit else None,
        "audit_external_side_effects": (
            audit.get("external_side_effects") if audit else None
        ),
        "audit_evidence_sha256": audit.get("evidence_sha256") if audit else None,
        "audit_chain_sha256": audit.get("chain_sha256") if audit else None,
        "source_commit": audit.get("source_commit") if audit else None,
        "deployment_source_commit": (
            audit.get("deployment_source_commit") if audit else None
        ),
        "vercel_deployment_id": (
            audit.get("vercel_deployment_id") if audit else None
        ),
        "manifest_status": manifest.get("status"),
        "manifest_root_valid": manifest.get("manifest_root_valid") is True,
        "chain_valid": manifest.get("chain_valid") is True,
        "manifest_root_sha256": manifest.get("manifest_root_sha256"),
        "chain_head_sha256": manifest.get("chain_head_sha256"),
    }


def ensure_release_gate_block_schema(db) -> None:
    db.execute(text("""
      CREATE TABLE IF NOT EXISTS release_gate_blocks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        release_candidate_id uuid NOT NULL
          REFERENCES release_candidates(id) ON DELETE CASCADE,
        attempted_decision text NOT NULL
          CHECK (attempted_decision IN ('APPROVE')),
        actor text NOT NULL,
        reason text NOT NULL,
        integrity_status text NOT NULL
          CHECK (integrity_status IN ('VERIFIED','TAMPERED','ORPHANED')),
        blocking_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
        live_acceptance_audit_id text,
        source_tree_sha256 text,
        audit_evidence_sha256 text,
        audit_chain_sha256 text,
        manifest_root_sha256 text,
        chain_head_sha256 text,
        vercel_deployment_id text,
        source_commit text,
        deployment_source_commit text,
        blocked_at timestamptz NOT NULL DEFAULT now()
      )
    """))
    db.execute(text("""
      CREATE INDEX IF NOT EXISTS idx_release_gate_blocks_candidate
      ON release_gate_blocks(release_candidate_id, blocked_at DESC)
    """))


def record_release_integrity_block(
    db,
    *,
    candidate_id,
    actor: str,
    reason: str,
    gate: dict[str, Any],
):
    ensure_release_gate_block_schema(db)
    return db.execute(text("""
      INSERT INTO release_gate_blocks(
        release_candidate_id,attempted_decision,actor,reason,
        integrity_status,blocking_reasons,live_acceptance_audit_id,
        source_tree_sha256,audit_evidence_sha256,audit_chain_sha256,
        manifest_root_sha256,chain_head_sha256,vercel_deployment_id,
        source_commit,deployment_source_commit)
      VALUES(
        :candidate_id,'APPROVE',:actor,:reason,:integrity_status,
        CAST(:blocking_reasons AS jsonb),:audit_id,:source_tree_sha256,
        :audit_evidence_sha256,:audit_chain_sha256,:manifest_root_sha256,
        :chain_head_sha256,:vercel_deployment_id,:source_commit,
        :deployment_source_commit)
      RETURNING id
    """), {
        "candidate_id": candidate_id,
        "actor": actor[:200],
        "reason": reason[:4000],
        "integrity_status": gate["integrity_status"],
        "blocking_reasons": json.dumps(
            gate.get("blocking_reasons") or [],
            ensure_ascii=False,
        ),
        "audit_id": gate.get("audit_id"),
        "source_tree_sha256": gate.get("source_tree_sha256"),
        "audit_evidence_sha256": gate.get("audit_evidence_sha256"),
        "audit_chain_sha256": gate.get("audit_chain_sha256"),
        "manifest_root_sha256": gate.get("manifest_root_sha256"),
        "chain_head_sha256": gate.get("chain_head_sha256"),
        "vercel_deployment_id": gate.get("vercel_deployment_id"),
        "source_commit": gate.get("source_commit"),
        "deployment_source_commit": gate.get("deployment_source_commit"),
    }).scalar_one()


def list_release_integrity_blocks(db, candidate_id) -> list[dict[str, Any]]:
    ensure_release_gate_block_schema(db)
    rows = db.execute(text("""
      SELECT id,attempted_decision,actor,reason,integrity_status,
             blocking_reasons,live_acceptance_audit_id,source_tree_sha256,
             audit_evidence_sha256,audit_chain_sha256,manifest_root_sha256,
             chain_head_sha256,vercel_deployment_id,source_commit,
             deployment_source_commit,blocked_at
      FROM release_gate_blocks
      WHERE release_candidate_id=:candidate_id
      ORDER BY blocked_at DESC,id DESC
      LIMIT 100
    """), {"candidate_id": candidate_id}).mappings().all()
    return [dict(row) for row in rows]
