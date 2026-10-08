from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_unified_production_homepage_route_and_security():
    client=TestClient(app)
    response=client.get("/")
    assert response.status_code==200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"]=="no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert 'lang="zh-CN"' in response.text
    assert "Unified Control Center v1.0" in response.text
    assert "PRODUCTION · READ ONLY" in response.text
    assert "Publishing Console" in response.text
    assert "Trust / KMS" in response.text
    assert "Governance" in response.text
    assert "Operations Console" in response.text
    assert "PREVIEW ONLY" in response.text


def test_production_homepage_static_assets_and_safe_read_only_api():
    client=TestClient(app)
    for path, media in (
        ("/review-assets/production-home.css", "text/css"),
        ("/review-assets/production-home.js", "javascript"),
    ):
        response=client.get(path)
        assert response.status_code==200
        assert media in response.headers["content-type"]

    root=Path(__file__).resolve().parents[1]/"app"/"static"
    js=(root/"production-home.js").read_text(encoding="utf-8")
    html=(root/"production-home.html").read_text(encoding="utf-8")
    css=(root/"production-home.css").read_text(encoding="utf-8")
    assert "innerHTML" not in js
    assert "eval(" not in js
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "fetch(" in js
    for path in (
        "/health","/v1/runs?limit=30",
        "/v1/opportunities?limit=50",
        "/v1/review-workspace?limit=50",
        "/v1/live-acceptance-audits?limit=100",
    ):
        assert path in js
    assert not any(marker in js for marker in ("method: 'POST'","method: \"POST\"","method: 'DELETE'","method: \"DELETE\""))
    assert "正式发布尚未开启" in html
    assert "未完成的真实跨云验收不会显示为已完成" in html
    assert "@media(max-width:750px)" in css
