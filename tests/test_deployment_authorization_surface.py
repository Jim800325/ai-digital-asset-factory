from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_migration_022_has_no_percent_placeholders():
    sql = (ROOT / "migrations" / "022_deployment_authorization_gate.sql").read_text(
        encoding="utf-8"
    )
    assert "%" not in sql
    assert "plan_row record;" in sql
    assert "CHECK (execution_enabled=false)" in sql
    assert "Deployment authorization never enables execution" in sql


def test_no_controlled_production_executor_surface_exists():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    module = (ROOT / "app" / "deployment_authorization.py").read_text(
        encoding="utf-8"
    )

    forbidden_route_fragments = (
        '"/v1/deploy"',
        '"/v1/deployment-execute"',
        '"/v1/deployment-executor"',
        '"/v1/promote"',
        '"/v1/rollback"',
    )
    for fragment in forbidden_route_fragments:
        assert fragment not in main

    forbidden_executor_tokens = (
        "subprocess.",
        "os.system(",
        "requests.post(",
        "httpx.post(",
        "vercel deploy",
        "vercel promote",
        "vercel rollback",
    )
    lower_module = module.lower()
    for token in forbidden_executor_tokens:
        assert token.lower() not in lower_module

    assert '"production_deployment_executed": False' in module
    assert '"execution_enabled": False' in module


def test_authorization_routes_are_plan_and_decision_only():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert (
        '@app.post("/v1/release-candidates/{candidate_id}/deployment-plan")'
        in main
    )
    assert '@app.get("/v1/deployment-plans/{plan_id}")' in main
    assert '@app.post("/v1/deployment-plans/{plan_id}/decision")' in main

    # Authorization may record readiness, but must not expose an execution action.
    assert '"deployment_executor":"DISABLED"' in main
    assert '"controlled_production_release":"AUTHORIZATION_ONLY"' in main
