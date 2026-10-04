from fastapi.testclient import TestClient

from app.main import app


client=TestClient(app)


def test_frontend_homepage_is_public_read_only_shell():
    response=client.get("/")
    assert response.status_code==200
    assert "AI Digital Asset Factory" in response.text
    assert "自动数字资产工厂" in response.text
    assert "进入控制中心" in response.text
    assert "/review-assets/system-home.css" in response.text
    assert "/review-assets/system-home.js" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"]=="no-store"


def test_backend_homepage_and_alias_share_control_center():
    for path in ("/admin","/control-center"):
        response=client.get(path)
        assert response.status_code==200
        assert "系统控制中心" in response.text
        assert "Human Review" in response.text
        assert "Publishing" in response.text
        assert "/review-assets/control-center.js" in response.text
        assert response.headers["x-content-type-options"]=="nosniff"


def test_homepage_assets_are_local_and_utf8():
    for path,marker in (
        ("/review-assets/system-home.css",".public-nav"),
        ("/review-assets/system-home.js","renderOpportunities"),
        ("/review-assets/control-center.css",".admin-sidebar"),
    ):
        response=client.get(path)
        assert response.status_code==200
        assert marker in response.text
        assert "�" not in response.text
