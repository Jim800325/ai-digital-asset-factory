from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_safe_unfreeze_ui_and_read_api_are_available():
    client=TestClient(app)
    page=client.get("/animation/reliability-review")
    assert page.status_code==200
    assert "SAFE UNFREEZE" in page.text
    assert "Recovery Evidence Gate" in page.text
    assert "Two-Person Restore Audit" in page.text

    response=client.get(
        "/v1/shrimp-animation/bilibili-reliability-safe-unfreeze"
    )
    assert response.status_code==200
    body=response.json()
    assert body["two_person_restore_required"] is True
    assert body["provider_writes"] is False
    assert body["secrets_redacted"] is True
    assert "eligible_for_restore" in body
    assert "recovery_evidence" in body
    assert "policy_control" in body


def test_first_restore_approval_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_restore_approval_key",
        "ci-restore-approval-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_restore_apply_key",
        "ci-restore-apply-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-reliability-restore-plans/"
        "00000000-0000-0000-0000-000000000001/first-approval",
        headers={
            "X-Shrimp-Bilibili-Reliability-Restore-Approval-Key":"wrong",
        },
        json={
            "decision":"APPROVE",
            "reason":"CI first restore gate",
            "actor":"first-approver",
            "plan_sha256":"0"*64,
            "dry_run_sha256":"1"*64,
        },
    )
    assert response.status_code==403


def test_second_restore_apply_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_restore_approval_key",
        "ci-restore-approval-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_restore_apply_key",
        "ci-restore-apply-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-reliability-restore-plans/"
        "00000000-0000-0000-0000-000000000001/second-apply",
        headers={
            "X-Shrimp-Bilibili-Reliability-Restore-Apply-Key":"wrong",
        },
        json={
            "decision":"APPROVE",
            "reason":"CI second restore gate",
            "actor":"second-approver",
            "plan_sha256":"0"*64,
            "dry_run_sha256":"1"*64,
        },
    )
    assert response.status_code==403


def test_restore_keys_must_be_independent(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_restore_approval_key",
        "same-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_restore_apply_key",
        "same-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-reliability-restore-plans/"
        "00000000-0000-0000-0000-000000000001/first-approval",
        headers={
            "X-Shrimp-Bilibili-Reliability-Restore-Approval-Key":"same-key",
        },
        json={
            "decision":"APPROVE",
            "reason":"CI restore key independence",
            "actor":"first-approver",
            "plan_sha256":"0"*64,
            "dry_run_sha256":"1"*64,
        },
    )
    assert response.status_code==503
