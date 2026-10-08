from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


def test_pipeline_console_pages_are_available_and_read_only():
    client=TestClient(app)

    root=client.get("/animation/pipeline")
    assert root.status_code == 200
    assert "Production Pipeline Explorer" in root.text
    assert "/review-assets/pipeline-console.js" in root.text
    assert "READ ONLY" in root.text

    deep=client.get(f"/animation/pipeline/{uuid4()}")
    assert deep.status_code == 200
    assert "Production Pipeline Explorer" in deep.text

    for forbidden in (
        "Upload Now",
        "Publish Now",
        "Run Cloud KMS",
        "Execute Production",
    ):
        assert forbidden not in root.text


def test_pipeline_console_summary_empty_is_safe(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "list_provider_jobs",
        lambda **kwargs: [],
    )
    client=TestClient(app)
    response=client.get("/v1/shrimp-animation/pipeline-console")
    assert response.status_code == 200
    payload=response.json()
    assert payload == {
        "mode":"READ_ONLY_PIPELINE_CONSOLE",
        "total_jobs":0,
        "jobs":[],
        "secrets_redacted":True,
    }


def test_control_center_and_publishing_link_pipeline_console():
    client=TestClient(app)

    homepage=client.get("/")
    assert homepage.status_code == 200
    assert 'href="/animation/pipeline"' in homepage.text
    assert "Production Pipeline" in homepage.text

    publishing=client.get("/animation-publishing")
    assert publishing.status_code == 200
    assert 'href="/animation/pipeline"' in publishing.text
    assert "Controlled Execution / Provider Evidence" in publishing.text
    assert "NO WRITE ACTIONS HERE" in publishing.text

    script=client.get("/review-assets/shrimp-publish.js")
    assert script.status_code == 200
    assert "/pipeline-console" in script.text
    assert "loadExecutionEvidence" in script.text
