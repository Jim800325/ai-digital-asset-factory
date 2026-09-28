from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_review_ui_static_security_contract():
    client=TestClient(app)
    response=client.get("/review")
    assert response.status_code==200
    assert response.headers["cache-control"]=="no-store"
    csp=response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "Human Review Workspace" in response.text

    static_dir=Path(__file__).resolve().parents[1]/"app"/"static"
    js=(static_dir/"review.js").read_text(encoding="utf-8")
    html=(static_dir/"review.html").read_text(encoding="utf-8")

    assert "innerHTML" not in js
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "review_package_sha256" in js
    assert "X-Release-Key" in js
    assert "部署永久禁用" in html
