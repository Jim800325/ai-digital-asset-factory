import pytest

from app.production_execution_adapter import (
    MockProductionExecutionAdapter,
    get_production_execution_adapter,
)
from app.production_release import (
    _normalize_artifact_path,
    _sha256,
    _transition_allowed,
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


def test_step1_adapter_registry_refuses_non_mock():
    assert get_production_execution_adapter("mock").kind == "MOCK"

    with pytest.raises(RuntimeError, match="supports MOCK only"):
        get_production_execution_adapter("VERCEL")
