from typing import Any

from app.live_acceptance_registry import (
    list_live_acceptance_audits,
    live_acceptance_integrity_manifest,
)


def evaluate_release_integrity(source_tree_sha256: str | None) -> dict[str, Any]:
    expected = (source_tree_sha256 or "").strip().lower()
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
        "source_tree_sha256": expected or None,
        "audit_id": audit.get("audit_id") if audit else None,
        "audit_acceptance_status": audit.get("acceptance_status") if audit else None,
        "audit_integrity_status": audit.get("integrity_status") if audit else None,
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
