import pytest

from app.config import settings
from app.sandbox_policy import execution_policy_snapshot, validate_execution_policy

def test_policy_snapshot_is_fail_closed():
    policy=execution_policy_snapshot(
        executor_kind="ACCEPTANCE",
        sandbox_image="python:3.12-slim",
    )
    assert policy["network"] == "DENY"
    assert policy["docker_network"] == "none"
    assert policy["filesystem_scope"] == "WORKSPACE_ONLY"
    assert policy["container_rootfs"] == "READ_ONLY"
    assert policy["capabilities"] == "DROP_ALL"
    assert policy["no_new_privileges"] is True
    assert policy["production_credentials"] == "DENY"
    assert policy["deployment"] == "DENY"
    assert policy["external_side_effects"] == "DENY"
    assert policy["host_docker_socket"] == "DENY"

def test_openhands_requires_explicit_enable(monkeypatch):
    monkeypatch.setattr(settings,"sandbox_execution_enabled",True)
    monkeypatch.setattr(settings,"openhands_enabled",False)
    monkeypatch.setattr(settings,"openhands_runtime","docker")
    with pytest.raises(RuntimeError,match="OpenHands sandbox adapter is disabled"):
        validate_execution_policy(
            executor_kind="OPENHANDS",
            sandbox_image="python:3.12-slim",
        )

def test_openhands_process_runtime_is_forbidden(monkeypatch):
    monkeypatch.setattr(settings,"sandbox_execution_enabled",True)
    monkeypatch.setattr(settings,"openhands_enabled",True)
    monkeypatch.setattr(settings,"openhands_runtime","process")
    with pytest.raises(RuntimeError,match="Docker sandbox is required"):
        validate_execution_policy(
            executor_kind="OPENHANDS",
            sandbox_image="python:3.12-slim",
        )

def test_disabled_sandbox_fails_closed(monkeypatch):
    monkeypatch.setattr(settings,"sandbox_execution_enabled",False)
    with pytest.raises(RuntimeError,match="Sandbox execution is disabled"):
        validate_execution_policy(
            executor_kind="ACCEPTANCE",
            sandbox_image="python:3.12-slim",
        )
