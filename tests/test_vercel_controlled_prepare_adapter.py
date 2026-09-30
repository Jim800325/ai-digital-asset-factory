import base64
import json

import httpx
import pytest

from app.production_execution_adapter import (
    ProviderWriteOutcomeUnknown,
    REAL_PRODUCTION_PROJECT_ID,
    VercelControlledExecutionAdapter,
)


TEAM_ID = "team_sacrificial"
PROJECT_ID = "prj_sacrificial"


def _snapshot(project_id: str = PROJECT_ID):
    raw = b'{"ok":true}\n'
    return {
        "id": "execution-1",
        "execution_sha256": "a" * 64,
        "plan_sha256": "b" * 64,
        "execution_bundle_sha256": "c" * 64,
        "target_provider": "VERCEL",
        "target_environment": "production",
        "target_project_id": project_id,
        "target_team_id": TEAM_ID,
        "execution_bundle": {
            "schema_version": "production-execution-bundle-v1",
            "artifacts": [
                {
                    "relative_path": "api/status.json",
                    "sha256": "d" * 64,
                    "byte_size": len(raw),
                    "media_type": "application/json",
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                }
            ],
        },
    }


def _adapter(handler, *, project_ids=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return VercelControlledExecutionAdapter(
        token="test-token",
        allowed_project_ids=project_ids or [PROJECT_ID],
        allowed_team_ids=[TEAM_ID],
        denied_project_ids=[],
        api_base="https://api.vercel.test",
        client=client,
    )


def test_real_production_project_is_hard_denylisted(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")

    adapter = VercelControlledExecutionAdapter(
        token="test-token",
        allowed_project_ids=[REAL_PRODUCTION_PROJECT_ID],
        allowed_team_ids=[TEAM_ID],
        denied_project_ids=[],
        api_base="https://api.vercel.test",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, request=request)
            )
        ),
    )

    with pytest.raises(RuntimeError, match="denylisted"):
        adapter.validate_target(_snapshot(REAL_PRODUCTION_PROJECT_ID))


def test_prepare_request_is_production_configured_but_skips_domains(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/v9/projects/{PROJECT_ID}"
        return httpx.Response(
            200,
            request=request,
            json={
                "id": PROJECT_ID,
                "name": "executor-sacrificial",
                "accountId": TEAM_ID,
            },
        )

    adapter = _adapter(handler)
    request = adapter.build_prepare_request(_snapshot())

    assert request.deployment_id.startswith("dpl_")
    assert len(request.request_sha256) == 64
    assert request.body["target"] == "production"
    assert request.body["autoAssignCustomDomains"] is False
    assert request.body["project"] == PROJECT_ID
    assert "deploymentId" not in request.body
    assert request.body["name"] == "executor-sacrificial"
    assert request.body["files"][0]["file"] == "api/status.json"
    assert request.body["files"][0]["encoding"] == "base64"
    assert request.body["meta"]["controlledMode"] == "SACRIFICIAL_PREPARE_ONLY"
    assert (
        request.body["meta"]["controlledReconciliationKey"]
        == request.deployment_id
    )
    assert len(seen) == 1


def test_prepare_timeout_is_ambiguous_and_never_retried(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    calls = {"get": 0, "post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            calls["get"] += 1
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": PROJECT_ID,
                    "name": "executor-sacrificial",
                    "accountId": TEAM_ID,
                },
            )
        if request.method == "POST":
            calls["post"] += 1
            raise httpx.ReadTimeout("ambiguous timeout", request=request)
        raise AssertionError(request.method)

    adapter = _adapter(handler)
    prepared = adapter.build_prepare_request(_snapshot())

    with pytest.raises(ProviderWriteOutcomeUnknown) as caught:
        adapter.send_prepare(prepared)

    assert caught.value.deployment_id == prepared.deployment_id
    assert len(caught.value.evidence_sha256) == 64
    assert calls == {"get": 1, "post": 1}


def test_prepare_success_requires_no_alias_assignment(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    calls = {"get": 0, "post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            calls["get"] += 1
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": PROJECT_ID,
                    "name": "executor-sacrificial",
                    "accountId": TEAM_ID,
                },
            )
        calls["post"] += 1
        body = json.loads(request.content.decode("utf-8"))
        assert body["target"] == "production"
        assert body["autoAssignCustomDomains"] is False
        assert "deploymentId" not in body
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "dpl_provider_assigned_123",
                "url": "executor-sacrificial-abc.vercel.app",
                "readyState": "READY",
                "projectId": PROJECT_ID,
                "teamId": TEAM_ID,
                "target": "production",
                "alias": [],
                "aliasAssigned": False,
                "autoAssignCustomDomains": False,
            },
        )

    adapter = _adapter(handler)
    prepared = adapter.build_prepare_request(_snapshot())
    candidate = adapter.send_prepare(prepared)

    assert candidate.state == "READY"
    assert candidate.deployment_id == "dpl_provider_assigned_123"
    assert candidate.provider_write_performed is True
    assert candidate.metadata["auto_assign_custom_domains"] is False
    assert candidate.metadata["production_traffic_changed"] is False
    assert calls == {"get": 1, "post": 1}


def test_reconciliation_is_read_only_and_404_does_not_replay(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    calls = {"get": 0, "post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            calls["get"] += 1
            return httpx.Response(404, request=request, json={"error": "not found"})
        calls["post"] += 1
        return httpx.Response(500, request=request)

    adapter = _adapter(handler)
    deployment_id = adapter.deterministic_deployment_id(
        _snapshot()["execution_sha256"]
    )
    result = adapter.read_candidate(_snapshot(), deployment_id)

    assert result is None
    assert calls == {"get": 1, "post": 0}


def test_metadata_recovery_is_read_only_and_matches_exact_execution(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    calls = {"list": 0, "detail": 0, "post": 0}
    snapshot = _snapshot()
    reconciliation_key = VercelControlledExecutionAdapter.deterministic_deployment_id(
        snapshot["execution_sha256"]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["post"] += 1
            return httpx.Response(500, request=request)
        if request.url.path == f"/v9/projects/{PROJECT_ID}":
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": PROJECT_ID,
                    "name": "executor-sacrificial",
                    "accountId": TEAM_ID,
                },
            )
        if request.url.path == "/v6/deployments":
            calls["list"] += 1
            assert request.url.params["projectId"] == PROJECT_ID
            assert request.url.params["target"] == "production"
            return httpx.Response(
                200,
                request=request,
                json={
                    "deployments": [
                        {"uid": "dpl_provider_assigned_123"}
                    ]
                },
            )
        if request.url.path == "/v13/deployments/dpl_provider_assigned_123":
            calls["detail"] += 1
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "dpl_provider_assigned_123",
                    "url": "executor-sacrificial-abc.vercel.app",
                    "readyState": "READY",
                    "projectId": PROJECT_ID,
                    "teamId": TEAM_ID,
                    "target": "production",
                    "alias": [],
                    "aliasAssigned": False,
                    "autoAssignCustomDomains": False,
                    "meta": {
                        "controlledExecutionId": snapshot["id"],
                        "controlledExecutionSha256": snapshot["execution_sha256"],
                        "controlledReconciliationKey": reconciliation_key,
                    },
                },
            )
        raise AssertionError(str(request.url))

    adapter = _adapter(handler)
    candidate = adapter.find_candidate_by_execution(snapshot)

    assert candidate is not None
    assert candidate.deployment_id == "dpl_provider_assigned_123"
    assert candidate.state == "READY"
    assert candidate.provider_write_performed is False
    assert candidate.metadata["source"] == "IMMUTABLE_METADATA_RECOVERY_READ"
    assert calls == {"list": 1, "detail": 1, "post": 0}
