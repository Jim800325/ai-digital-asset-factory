import pytest

from app.config import settings
from app.sandbox_policy import execution_policy_snapshot, validate_execution_policy

def test_acceptance_policy_is_fail_closed():
    policy=execution_policy_snapshot(
        executor_kind="ACCEPTANCE",
        sandbox_image="python:3.12-slim",
    )
    assert policy["outer_sandbox"] == "HARDENED_DOCKER"
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

def test_openhands_policy_uses_internal_gateway_only():
    policy=execution_policy_snapshot(
        executor_kind="OPENHANDS",
        sandbox_image="asset-factory-openhands:1.16.0",
    )
    assert policy["outer_sandbox"] == "HARDENED_DOCKER"
    assert policy["inner_runtime"] == "process"
    assert policy["network"] == "INTERNAL_GATEWAY_ONLY"
    assert policy["docker_network"] == "internal-gateway"
    assert policy["host_docker_socket"] == "DENY"
    assert policy["deployment"] == "DENY"

def test_openhands_requires_explicit_enable(monkeypatch):
    monkeypatch.setattr(settings,"sandbox_execution_enabled",True)
    monkeypatch.setattr(settings,"openhands_enabled",False)
    monkeypatch.setattr(settings,"openhands_runtime","process")
    with pytest.raises(RuntimeError,match="OpenHands sandbox adapter is disabled"):
        validate_execution_policy(
            executor_kind="OPENHANDS",
            sandbox_image="asset-factory-openhands:1.16.0",
        )

def test_openhands_host_docker_runtime_is_rejected(monkeypatch):
    monkeypatch.setattr(settings,"sandbox_execution_enabled",True)
    monkeypatch.setattr(settings,"openhands_enabled",True)
    monkeypatch.setattr(settings,"openhands_runtime","docker")
    monkeypatch.setattr(settings,"openhands_model","openai/mock")
    with pytest.raises(RuntimeError,match="hardened outer Docker sandbox"):
        validate_execution_policy(
            executor_kind="OPENHANDS",
            sandbox_image="asset-factory-openhands:1.16.0",
        )

def test_openhands_mock_mode_can_pass_without_upstream_secret(monkeypatch):
    monkeypatch.setattr(settings,"sandbox_execution_enabled",True)
    monkeypatch.setattr(settings,"openhands_enabled",True)
    monkeypatch.setattr(settings,"openhands_runtime","process")
    monkeypatch.setattr(settings,"openhands_model","openai/mock")
    monkeypatch.setattr(settings,"openhands_gateway_mode","MOCK")
    policy=validate_execution_policy(
        executor_kind="OPENHANDS",
        sandbox_image="asset-factory-openhands:1.16.0",
    )
    assert policy["network"] == "INTERNAL_GATEWAY_ONLY"

def test_openhands_proxy_requires_https_upstream(monkeypatch):
    monkeypatch.setattr(settings,"sandbox_execution_enabled",True)
    monkeypatch.setattr(settings,"openhands_enabled",True)
    monkeypatch.setattr(settings,"openhands_runtime","process")
    monkeypatch.setattr(settings,"openhands_model","openai/example")
    monkeypatch.setattr(settings,"openhands_gateway_mode","PROXY")
    monkeypatch.setattr(settings,"openhands_llm_upstream_url","http://example.com/v1")
    monkeypatch.setattr(settings,"openhands_llm_api_key","secret")
    with pytest.raises(RuntimeError,match="must be HTTPS"):
        validate_execution_policy(
            executor_kind="OPENHANDS",
            sandbox_image="asset-factory-openhands:1.16.0",
        )

def test_disabled_sandbox_fails_closed(monkeypatch):
    monkeypatch.setattr(settings,"sandbox_execution_enabled",False)
    with pytest.raises(RuntimeError,match="Sandbox execution is disabled"):
        validate_execution_policy(
            executor_kind="ACCEPTANCE",
            sandbox_image="python:3.12-slim",
        )
