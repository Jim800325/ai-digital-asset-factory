from fastapi.testclient import TestClient
from app.config import settings
from app.main import app

def test_recovery_decision_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(settings,"shrimp_bilibili_recovery_approval_key","ci-recovery-key")
    monkeypatch.setattr(settings,"shrimp_bilibili_live_acceptance_key","ci-live-key")
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-recovery-approvals/00000000-0000-0000-0000-000000000001/decision",
        headers={"X-Shrimp-Bilibili-Recovery-Approval-Key":"wrong"},
        json={"decision":"APPROVE","reason":"CI gate check","actor":"ci"},
    )
    assert response.status_code==403

def test_recovery_apply_rejects_wrong_live_key(monkeypatch):
    monkeypatch.setattr(settings,"shrimp_bilibili_live_acceptance_key","ci-live-key")
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-recovery-approvals/00000000-0000-0000-0000-000000000001/apply",
        headers={"X-Shrimp-Bilibili-Live-Acceptance-Key":"wrong"},
        json={"actor":"ci"},
    )
    assert response.status_code==403
