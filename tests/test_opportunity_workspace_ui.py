from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_opportunity_workspace_routes_are_read_only_and_zh_hk():
    client = TestClient(app)

    response = client.get("/opportunities")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert 'lang="zh-HK"' in response.text
    assert "SBW-2 · OPPORTUNITY WORKSPACE" in response.text
    assert "<h1>機會池</h1>" in response.text
    assert "FILTER / SORT" in response.text
    assert "OPPORTUNITY BOARD" in response.text
    assert "SBW-3 才會啟動 Evidence Expansion Engine" in response.text

    detail = client.get(
        "/opportunities/00000000-0000-0000-0000-000000000000"
    )
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    assert 'lang="zh-HK"' in detail.text
    assert "SBW-2 · OPPORTUNITY DETAIL" in detail.text
    assert "機會生命週期" in detail.text
    assert "為什麼值得關注" in detail.text
    assert "為什麼還不能做" in detail.text
    assert "證據來源" in detail.text
    assert "BUILD_READY / Proposal" in detail.text


def test_opportunity_workspace_static_assets_do_not_add_write_paths_or_browser_storage():
    client = TestClient(app)
    assets = (
        ("/review-assets/opportunities.css", "text/css"),
        ("/review-assets/opportunities.js", "javascript"),
        ("/review-assets/opportunity-detail.css", "text/css"),
        ("/review-assets/opportunity-detail.js", "javascript"),
    )
    for path, media in assets:
        response = client.get(path)
        assert response.status_code == 200
        assert media in response.headers["content-type"]

    root = Path(__file__).resolve().parents[1] / "app" / "static"
    list_js = (root / "opportunities.js").read_text(encoding="utf-8")
    detail_js = (root / "opportunity-detail.js").read_text(encoding="utf-8")
    list_html = (root / "opportunities.html").read_text(encoding="utf-8")
    detail_html = (root / "opportunity-detail.html").read_text(encoding="utf-8")

    for source in (list_js, detail_js):
        assert "innerHTML" not in source
        assert "eval(" not in source
        assert "localStorage" not in source
        assert "sessionStorage" not in source
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert f'method: "{method}"' not in source
            assert f"method: '{method}'" not in source

    assert 'fetchJson("/v1/opportunity-workspace?"' in list_js
    assert 'fetchJson("/v1/opportunity-workspace/"' in detail_js
    assert "hydrateFromLocation()" in list_js
    assert 'href="/system"' in list_html
    assert 'href="/opportunities"' in detail_html


def test_opportunity_workspace_ui_keeps_sb3_evidence_expansion_non_executable():
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    list_html = (root / "opportunities.html").read_text(encoding="utf-8")
    detail_js = (root / "opportunity-detail.js").read_text(encoding="utf-8")

    assert "SBW-3 才會啟動 Evidence Expansion Engine" in list_html
    assert "EXPAND_EVIDENCE" not in list_html
    assert "/v1/runs" not in detail_js
    assert "/decision" not in detail_js

    forbidden_simplified = (
        "必须先通过",
        "进入 CANDIDATE",
        "生成后才会执行",
        "已满足",
        "尚未满足",
    )
    for phrase in forbidden_simplified:
        assert phrase not in detail_js
