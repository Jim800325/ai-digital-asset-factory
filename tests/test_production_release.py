import base64
import hashlib

import pytest

from app.production_execution_adapter import (
    MockProductionExecutionAdapter,
    get_production_execution_adapter,
)
from app.production_release import (
    _normalize_artifact_path,
    _production_execution_decision_material,
    _sha256,
    _stored_chain_is_verified_ancestor,
    _transition_allowed,
    _validate_execution_bundle_snapshot,
)


def test_execution_hash_is_deterministic():
    value = {
        "schema_version": "production-release-execution-v1",
        "deployment_plan_id": "plan-1",
        "execution_bundle_sha256": "a" * 64,
        "production_execution_enabled": False,
    }
    reversed_value = dict(reversed(list(value.items())))
    assert _sha256(value) == _sha256(reversed_value)


def test_execution_artifact_path_validation_is_fail_closed():
    assert _normalize_artifact_path("app/main.py") == "app/main.py"

    for value in (
        "",
        "/etc/passwd",
        "../secret",
        "app/../secret",
        "app//main.py",
        "app\\main.py",
        "folder/",
    ):
        with pytest.raises(RuntimeError):
            _normalize_artifact_path(value)


def test_execution_state_machine_allows_only_declared_edges():
    assert _transition_allowed("SNAPSHOT_CREATED", "PREPARING")
    assert _transition_allowed("PREPARING", "PREPARE_UNKNOWN")
    assert _transition_allowed("PREPARE_UNKNOWN", "READY_FOR_PROMOTION")
    assert _transition_allowed("PREPARING", "READY_FOR_PROMOTION")
    assert _transition_allowed("READY_FOR_PROMOTION", "PROMOTION_REQUESTED")
    assert _transition_allowed("PROMOTION_UNKNOWN", "PRODUCTION_ACTIVE")
    assert _transition_allowed("ROLLBACK_REQUIRED", "ROLLBACK_REQUESTED")
    assert _transition_allowed("ROLLBACK_UNKNOWN", "ROLLED_BACK")

    assert not _transition_allowed("SNAPSHOT_CREATED", "PRODUCTION_ACTIVE")
    assert not _transition_allowed("READY_FOR_PROMOTION", "ROLLED_BACK")
    assert not _transition_allowed("ROLLED_BACK", "PRODUCTION_ACTIVE")


def test_mock_adapter_is_deterministic_and_side_effect_free():
    adapter = MockProductionExecutionAdapter()
    snapshot = {"execution_sha256": "a" * 64}

    first = adapter.prepare_candidate(snapshot)
    second = adapter.prepare_candidate(snapshot)

    assert first == second
    assert first.state == "READY"
    assert first.deployment_id.startswith("mock_dpl_")
    assert first.url.endswith(".mock.invalid")
    assert first.provider_write_performed is False
    assert first.metadata["external_side_effects"] == "DENY"
    assert first.metadata["production_traffic_changed"] is False

    with pytest.raises(RuntimeError, match="does not implement Production promotion"):
        adapter.promote(snapshot)

    with pytest.raises(RuntimeError, match="does not implement Production rollback"):
        adapter.rollback(snapshot)


def test_step4_adapter_registry_is_explicit():
    assert get_production_execution_adapter("mock").kind == "MOCK"
    assert (
        get_production_execution_adapter(
            "VERCEL_CONTROLLED_EXECUTOR",
            token="test-token",
            allowed_project_ids=["prj_sacrificial"],
            allowed_team_ids=["team_sacrificial"],
            denied_project_ids=[],
            api_base="https://api.vercel.test",
        ).kind
        == "VERCEL_CONTROLLED_EXECUTOR"
    )

    with pytest.raises(RuntimeError, match="Unsupported"):
        get_production_execution_adapter("VERCEL")



def test_execution_integrity_bundle_revalidates_exact_bytes():
    raw = b"immutable artifact bytes"
    artifact_sha = hashlib.sha256(raw).hexdigest()
    bundle = {
        "schema_version": "production-execution-bundle-v1",
        "review_package_id": "review-1",
        "review_package_sha256": "b" * 64,
        "source_tree_sha256": "c" * 64,
        "artifacts": [
            {
                "relative_path": "dist/app.txt",
                "sha256": artifact_sha,
                "byte_size": len(raw),
                "media_type": "text/plain",
                "content_base64": base64.b64encode(raw).decode("ascii"),
            }
        ],
    }
    row = {
        "review_package_id": "review-1",
        "review_package_sha256": "b" * 64,
        "source_tree_sha256": "c" * 64,
        "execution_bundle": bundle,
        "execution_bundle_sha256": _sha256(bundle),
    }

    assert _validate_execution_bundle_snapshot(row) == []

    tampered_bundle = dict(bundle)
    tampered_artifact = dict(bundle["artifacts"][0])
    tampered_artifact["content_base64"] = base64.b64encode(
        b"mutated bytes"
    ).decode("ascii")
    tampered_bundle["artifacts"] = [tampered_artifact]
    tampered_row = dict(row)
    tampered_row["execution_bundle"] = tampered_bundle

    reasons = _validate_execution_bundle_snapshot(tampered_row)
    assert "execution_bundle_artifact_sha256_mismatch" in reasons
    assert "execution_bundle_artifact_byte_size_mismatch" in reasons
    assert "execution_bundle_sha256_mismatch" in reasons


def test_execution_integrity_allows_verified_append_only_chain_extension():
    stored_head = "a" * 64
    current_head = "b" * 64
    manifest = {
        "status": "VERIFIED",
        "manifest_root_valid": True,
        "chain_valid": True,
        "chain_head_sha256": current_head,
        "entries": {
            "audit-old": {
                "status": "VERIFIED",
                "chain_sha256": stored_head,
            },
            "audit-new": {
                "status": "VERIFIED",
                "chain_sha256": current_head,
            },
        },
    }

    assert _stored_chain_is_verified_ancestor(manifest, stored_head)
    assert _stored_chain_is_verified_ancestor(manifest, current_head)
    assert not _stored_chain_is_verified_ancestor(manifest, "c" * 64)

    broken = dict(manifest)
    broken["chain_valid"] = False
    assert not _stored_chain_is_verified_ancestor(broken, stored_head)



def test_human_production_decision_material_binds_candidate_and_is_side_effect_free():
    row = {
        "id": "execution-1",
        "execution_sha256": "a" * 64,
        "deployment_plan_id": "plan-1",
        "plan_sha256": "b" * 64,
        "candidate_vercel_deployment_id": "mock_dpl_123",
        "candidate_vercel_url": "https://mock_dpl_123.mock.invalid",
        "target_project_id": "prj_target",
        "target_team_id": "team_target",
    }
    material = _production_execution_decision_material(
        row,
        decision="PROMOTE",
        reason="approved for controlled promotion",
        actor="ci-human-production",
        candidate_verified_event_id="event-1",
        candidate_provider_result_sha256="c" * 64,
        integrity_check_id="check-1",
    )

    assert material["candidate_vercel_deployment_id"] == "mock_dpl_123"
    assert material["target_project_id"] == "prj_target"
    assert material["provider_write_performed"] is False
    assert material["production_traffic_changed"] is False
    assert _sha256(material) == _sha256(dict(reversed(list(material.items()))))
