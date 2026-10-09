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


def test_production_homepage_static_assets_use_only_sanitized_summary_api():
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
    assert 'fetchJson("/v1/control-center-summary")' in js

    for raw_path in (
        "/health",
        "/v1/runs?limit=30",
        "/v1/opportunities?limit=50",
        "/v1/review-workspace?limit=50",
        "/v1/live-acceptance-audits?limit=100",
    ):
        assert raw_path not in js

    for method in ("POST","PUT","PATCH","DELETE"):
        assert f'method: "{method}"' not in js
        assert f"method: '{method}'" not in js

    assert "正式发布尚未开启" in html
    assert "未完成的真实跨云验收不会显示为已完成" in html
    assert "@media(max-width:750px)" in css


def test_control_center_summary_is_minimized_and_filters_test_fixtures():
    client=TestClient(app)
    response=client.get("/v1/control-center-summary")
    assert response.status_code==200
    payload=response.json()

    assert set(payload)=={
        "status","mode","database","migrations","release_deployment",
        "metrics","recent_runs","recent_opportunities",
    }
    assert payload["mode"]=="OBSERVE"
    assert payload["release_deployment"]=="DISABLED"
    assert set(payload["database"])=={"status","available"}
    assert set(payload["migrations"])=={"status","expected_count","applied_count"}
    assert set(payload["metrics"])=={
        "pipeline_runs","opportunities","human_reviews","audit_records"
    }

    for opportunity in payload["recent_opportunities"]:
        assert not str(opportunity.get("title") or "").startswith("[TEST_ONLY]")
        assert set(opportunity)=={
            "title","score","independent_source_count","status"
        }

    for run in payload["recent_runs"]:
        assert set(run)=={"id","status","evidence_created","started_at"}

    serialized=response.text.lower()
    forbidden=(
        "source_commit",
        "deployment_source_commit",
        "vercel_deployment_id",
        "source_tree_sha256",
        "evidence_sha256",
        "chain_sha256",
        "manifest_root_sha256",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "evidence_filename",
        "audit_id",
        "provider",
        "model",
    )
    for key in forbidden:
        assert key not in serialized
