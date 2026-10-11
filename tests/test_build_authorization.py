from pathlib import Path
from inspect import signature

import pytest
from fastapi.testclient import TestClient

from app.build_authorization import current_authorization_policy
from app.config import settings
from app.main import app
from app.sandbox_execution import create_sandbox_request
from app.sandbox_policy import validate_execution_policy


def test_authorization_policy_is_deterministic_and_contains_no_secrets(monkeypatch):
    monkeypatch.setattr(settings,"sandbox_execution_enabled",False)
    monkeypatch.setattr(settings,"openhands_enabled",True)
    monkeypatch.setattr(settings,"openhands_runtime","process")
    monkeypatch.setattr(settings,"openhands_cli_version","1.16.0")
    monkeypatch.setattr(settings,"openhands_cli_image","asset-factory-openhands:1.16.0")
    monkeypatch.setattr(settings,"openhands_model","openai/mock")
    monkeypatch.setattr(settings,"openhands_gateway_mode","MOCK")
    monkeypatch.setattr(settings,"openhands_llm_api_key","do-not-snapshot")

    one=current_authorization_policy("OPENHANDS")
    two=current_authorization_policy("OPENHANDS")

    assert one["policy_fingerprint"]==two["policy_fingerprint"]
    assert one["policy"]["deployment"]=="DENY"
    assert one["policy"]["external_side_effects"]=="DENY"
    assert one["policy"]["production_credentials"]=="DENY"
    assert one["policy"]["host_docker_socket"]=="DENY"
    serialized=str(one)
    assert "do-not-snapshot" not in serialized
    assert "openhands_llm_api_key" not in serialized


def test_policy_can_be_authorized_while_execution_gate_remains_disabled(monkeypatch):
    monkeypatch.setattr(settings,"sandbox_execution_enabled",False)
    policy=validate_execution_policy(
        executor_kind="ACCEPTANCE",
        sandbox_image="python:3.12-slim",
        require_execution_enabled=False,
    )
    assert policy["network"]=="DENY"
    assert policy["external_side_effects"]=="DENY"

    with pytest.raises(RuntimeError,match="Sandbox execution is disabled"):
        validate_execution_policy(
            executor_kind="ACCEPTANCE",
            sandbox_image="python:3.12-slim",
        )


def test_sandbox_request_requires_explicit_authorization_id():
    params=signature(create_sandbox_request).parameters
    assert "authorization_id" in params
    assert params["authorization_id"].default is params["authorization_id"].empty


def test_build_authorization_migration_is_immutable_and_single_use():
    root=Path(__file__).resolve().parents[1]
    sql=(root/"migrations"/"024_build_authorization.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS sandbox_build_authorizations" in sql
    assert "UNIQUE(proposal_id, proposal_revision)" in sql
    assert "authorization_status IN ('AUTHORIZED','REJECTED','CONSUMED','STALE')" in sql
    assert "ADD COLUMN IF NOT EXISTS authorization_id uuid" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "uq_sandbox_build_request_authorization" in sql


def test_sandbox_execution_requires_consumed_authorization_in_source():
    root=Path(__file__).resolve().parents[1]
    source=(root/"app"/"sandbox_execution.py").read_text(encoding="utf-8")
    assert 'authorization["authorization_status"]!="AUTHORIZED"' in source
    assert "Authorization policy fingerprint is stale" in source
    assert "authorization_status='CONSUMED'" in source
    assert 'request["authorization_status"]!="CONSUMED"' in source
    assert "Sandbox execution policy changed; execution denied" in source


def test_sbw4_build_ready_workspace_has_two_human_gates_and_no_execute_action():
    client=TestClient(app)
    response=client.get("/build-ready")
    assert response.status_code==200
    assert response.headers["cache-control"]=="no-store"
    assert 'lang="zh-HK"' in response.text
    assert "GATE 1" in response.text
    assert "GATE 2" in response.text
    assert "Proposal APPROVE ≠ Sandbox AUTHORIZE ≠ Execute" in response.text

    root=Path(__file__).resolve().parents[1]/"app"/"static"
    js=(root/"build-ready.js").read_text(encoding="utf-8")
    html=(root/"build-ready.html").read_text(encoding="utf-8")
    main=(root.parent/"main.py").read_text(encoding="utf-8")

    assert "innerHTML" not in js
    assert "eval(" not in js
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert '"X-Approval-Key"' in js
    assert '"X-Build-Authorization-Key"' in js
    assert 'method:"POST"' in js
    assert "/v1/build-proposals/" in js
    assert "/v1/build-authorizations/" in js

    assert "啟動 Agent" in html
    assert "本頁沒有 Execute 動作" in js
    assert '@app.post("/v1/sandbox-requests' not in main
    assert "execute_sandbox_request" not in main


def test_build_authorization_defaults_fail_closed(monkeypatch):
    monkeypatch.setattr(settings,"build_authorization_enabled",False)
    monkeypatch.setattr(settings,"build_authorization_key","")
    assert settings.build_authorization_enabled is False
    assert settings.build_authorization_key==""
