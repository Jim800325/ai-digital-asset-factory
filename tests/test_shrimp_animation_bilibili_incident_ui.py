from fastapi.testclient import TestClient
from app.main import app

def test_incident_operations_ui_and_read_apis():
    client=TestClient(app)
    page=client.get("/animation/operations")
    assert page.status_code==200
    assert "PUBLISHER INCIDENTS" in page.text
    assert "RECOVERY APPROVALS" in page.text
    assert "OPERATIONS NOTIFICATIONS" in page.text
    assert client.get("/v1/shrimp-animation/bilibili-incidents").status_code==200
    assert client.get("/v1/shrimp-animation/bilibili-recovery-approvals").status_code==200
    assert client.get("/v1/shrimp-animation/bilibili-notifications").status_code==200
