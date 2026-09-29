import pytest

from app.deployment_authorization import (
    _authorization_drift_reasons,
    _clean_target,
    _plan_material,
    _require_integrity_fields,
    _sha256,
)


def _integrity(**overrides):
    value = {
        "allowed": True,
        "integrity_status": "VERIFIED",
        "blocking_reasons": [],
        "acceptance_provenance_tree_sha256": "a" * 64,
        "audit_id": "8b385170ee00c667",
        "audit_evidence_sha256": "b" * 64,
        "audit_chain_sha256": "c" * 64,
        "manifest_root_sha256": "d" * 64,
        "chain_head_sha256": "e" * 64,
        "source_commit": "1" * 40,
        "deployment_source_commit": "1" * 40,
        "vercel_deployment_id": "dpl_verified",
    }
    value.update(overrides)
    return value


def _row(**overrides):
    value = {
        "release_status": "RELEASE_APPROVED",
        "candidate_deployment_enabled": False,
        "archived_at": None,
        "release_decision": "APPROVE",
        "release_decision_status": "RELEASE_APPROVED",
        "decision_review_package_id": "pkg-1",
        "review_package_id": "pkg-1",
        "decision_review_package_sha256": "f" * 64,
        "review_package_sha256": "f" * 64,
        "decision_source_tree_sha256": "9" * 64,
        "source_tree_sha256": "9" * 64,
        "package_status": "GENERATED",
        "content_snapshot_complete": True,
        "current_package_sha256": "f" * 64,
        "current_source_tree_sha256": "9" * 64,
        "acceptance_provenance_tree_sha256": "a" * 64,
        "live_acceptance_audit_id": "8b385170ee00c667",
        "audit_evidence_sha256": "b" * 64,
        "audit_chain_sha256": "c" * 64,
        "manifest_root_sha256": "d" * 64,
        "chain_head_sha256": "e" * 64,
        "source_commit": "1" * 40,
        "deployment_source_commit": "1" * 40,
        "source_vercel_deployment_id": "dpl_verified",
    }
    value.update(overrides)
    return value


def test_deployment_plan_hash_is_deterministic():
    material = _plan_material(
        release_candidate_id="candidate-1",
        release_decision_id="decision-1",
        review_package_id="package-1",
        target_project_id="prj_target",
        target_team_id="team_target",
        review_package_sha256="f" * 64,
        source_tree_sha256="9" * 64,
        integrity=_integrity(),
    )

    assert _sha256(material) == _sha256(dict(reversed(list(material.items()))))
    assert material["execution_enabled"] is False
    assert material["target_environment"] == "production"
    assert material["target_provider"] == "VERCEL"


def test_target_identifier_validation_is_fail_closed():
    assert _clean_target("prj_abc-123", field="target_project_id") == "prj_abc-123"

    for value in ("", "ab", "../secret", "project id", "x" * 161):
        with pytest.raises(ValueError):
            _clean_target(value, field="target_project_id")


def test_deployment_plan_requires_complete_verified_integrity():
    _require_integrity_fields(_integrity())

    with pytest.raises(RuntimeError):
        _require_integrity_fields(
            _integrity(allowed=False, blocking_reasons=["manifest_root_invalid"])
        )

    with pytest.raises(RuntimeError):
        _require_integrity_fields(_integrity(audit_chain_sha256=None))


def test_authorization_has_no_drift_when_release_and_integrity_match():
    assert _authorization_drift_reasons(_row(), _integrity()) == []


def test_authorization_detects_release_review_and_provenance_drift():
    row = _row(
        release_status="READY_FOR_REVIEW",
        current_package_sha256="0" * 64,
        manifest_root_sha256="7" * 64,
    )
    integrity = _integrity(
        allowed=False,
        blocking_reasons=["chain_invalid"],
        manifest_root_sha256="d" * 64,
    )

    reasons = _authorization_drift_reasons(row, integrity)

    assert "release_candidate_not_approved" in reasons
    assert "review_package_binding_drift" in reasons
    assert "integrity_chain_invalid" in reasons
    assert "manifest_root_sha256_drift" in reasons


def test_authorization_detects_archived_or_enabled_candidate():
    reasons = _authorization_drift_reasons(
        _row(
            archived_at="2026-09-29T00:00:00Z",
            candidate_deployment_enabled=True,
        ),
        _integrity(),
    )

    assert "release_candidate_archived" in reasons
    assert "candidate_deployment_enabled_unexpectedly" in reasons
