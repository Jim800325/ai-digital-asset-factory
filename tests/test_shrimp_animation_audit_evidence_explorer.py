import json

from fastapi.testclient import TestClient

from app.main import app
from app.providers.animation.shrimp.audit_evidence_explorer import (
    CATEGORIES,
    _filter_events,
)


def test_audit_evidence_workspace_is_read_only():
    client=TestClient(app)
    page=client.get("/animation/audit-evidence")
    assert page.status_code==200
    assert "Audit / Evidence Explorer" in page.text
    assert "READ ONLY" in page.text
    assert "SECRETS REDACTED" in page.text
    assert "Export JSON" in page.text
    assert "Export CSV" in page.text


def test_audit_evidence_api_contract_and_safety():
    body=TestClient(app).get(
        "/v1/shrimp-animation/audit-evidence",
        params={"limit":25},
    ).json()
    assert body["mode"]=="READ_ONLY_AUDIT_EVIDENCE_EXPLORER"
    assert body["schema_version"]=="unified-audit-evidence-v1"
    assert set(body["categories"])==set(CATEGORIES)
    assert body["pagination"]["limit"]==25
    assert body["safety"]=={
        "read_only":True,
        "raw_secret_snapshots_exposed":False,
        "credentials_exposed":False,
        "private_keys_exposed":False,
        "production_writes":False,
        "provider_writes":False,
    }
    assert body["secrets_redacted"] is True
    for event in body["events"]:
        assert "details" in event
        assert "evidence_sha256" in event
        assert "occurred_at" in event
        assert "source_table" in event
        serialized=json.dumps(event,sort_keys=True).lower()
        assert "private_key_pem" not in serialized
        assert "cookie_value" not in serialized
        assert "authorization:" not in serialized


def test_audit_evidence_filtering_is_deterministic():
    events=[
        {
            "id":"incident:1","category":"INCIDENT","kind":"INCIDENT_OPENED",
            "status":"OPEN","severity":"CRITICAL","title":"BILI-1",
            "source_table":"incidents","source_id":"1","parent_id":None,
            "occurred_at":"2026-10-08T10:00:00+00:00","actor":"ops",
            "evidence_sha256":"a"*64,"immutable":True,
            "details":{"incident_key":"BILI-1"},
        },
        {
            "id":"trust_root:2","category":"TRUST_ROOT","kind":"TUF_ROOT",
            "status":"ACTIVE","severity":"INFO","title":"TUF Root v1",
            "source_table":"roots","source_id":"2","parent_id":None,
            "occurred_at":"2026-10-08T11:00:00+00:00","actor":"trust",
            "evidence_sha256":"b"*64,"immutable":True,
            "details":{"root_version":1},
        },
    ]
    filtered=_filter_events(
        events,
        category="TRUST_ROOT",
        status="ACTIVE",
        q="root",
        after="2026-10-08T10:30:00Z",
        before="2026-10-08T11:30:00Z",
    )
    assert [item["id"] for item in filtered]==["trust_root:2"]


def test_audit_evidence_exports_json_and_csv():
    client=TestClient(app)
    json_response=client.get(
        "/v1/shrimp-animation/audit-evidence/export",
        params={"format":"json"},
    )
    assert json_response.status_code==200
    assert "attachment;" in json_response.headers["content-disposition"]
    assert json_response.headers["content-disposition"].endswith('.json"')
    payload=json_response.json()
    assert payload["schema_version"]=="unified-audit-evidence-v1"
    assert payload["secrets_redacted"] is True

    csv_response=client.get(
        "/v1/shrimp-animation/audit-evidence/export",
        params={"format":"csv"},
    )
    assert csv_response.status_code==200
    assert csv_response.headers["content-disposition"].endswith('.csv"')
    assert "occurred_at,category,kind,status" in csv_response.text


def test_audit_evidence_rejects_invalid_export_format():
    response=TestClient(app).get(
        "/v1/shrimp-animation/audit-evidence/export",
        params={"format":"xml"},
    )
    assert response.status_code==422


def test_control_center_links_to_unified_evidence_explorer():
    client=TestClient(app)
    page=client.get("/")
    assert page.status_code==200
    assert "/animation/audit-evidence" in page.text
    assert "Unified Audit Timeline" in page.text

    js=client.get("/review-assets/control-center.js")
    assert js.status_code==200
    assert "/v1/shrimp-animation/audit-evidence?limit=12" in js.text

    explorer_js=client.get("/review-assets/audit-evidence-explorer.js")
    assert explorer_js.status_code==200
    assert "/v1/shrimp-animation/audit-evidence?" in explorer_js.text
    assert "/v1/shrimp-animation/audit-evidence/export?" in explorer_js.text
    assert 'method:"POST"' not in explorer_js.text
    assert "fetch(" in explorer_js.text
