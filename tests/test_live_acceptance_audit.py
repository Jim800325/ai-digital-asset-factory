from sqlalchemy import text

from app.db import engine
from app.live_acceptance_audit import (
    claim_live_acceptance,
    duplicate_response,
    ensure_live_acceptance_audit_schema,
    finish_live_acceptance,
    update_live_acceptance_phase,
)


def test_live_acceptance_claim_is_idempotent_and_persistent():
    ensure_live_acceptance_audit_schema()
    trigger_hash="a"*64

    first=claim_live_acceptance(trigger_hash,model="gpt-5.6-luna")
    assert first["claimed"] is True
    assert first["acceptance_status"] == "RUNNING"
    assert first["duplicate_requests"] == 0

    update_live_acceptance_phase(first["id"],"run_openhands")

    second=claim_live_acceptance(trigger_hash,model="gpt-5.6-luna")
    assert second["claimed"] is False
    assert second["acceptance_status"] == "RUNNING"
    assert second["phase"] == "run_openhands"
    assert second["duplicate_requests"] == 1

    in_progress=duplicate_response(second)
    assert in_progress["acceptance_status"] == "IN_PROGRESS"
    assert in_progress["duplicate_suppressed"] is True
    assert in_progress["gateway_request_count"] == 0

    result={
        "acceptance_status":"PASSED",
        "phase":"complete",
        "audit_id":first["id"],
        "provider":"AIHUBMIX",
        "model":"gpt-5.6-luna",
        "gateway_mode":"PROXY",
        "live_model_verified":True,
        "budget_status":"WITHIN_BUDGET",
        "gateway_request_count":2,
        "prompt_tokens":100,
        "completion_tokens":20,
        "total_tokens":120,
        "estimated_cost_usd":0.001,
        "model_request_observed":True,
        "tests_passed":True,
        "artifact_count":3,
        "source_tree_sha256":"b"*64,
    }
    finish_live_acceptance(first["id"],result)

    third=claim_live_acceptance(trigger_hash,model="gpt-5.6-luna")
    assert third["claimed"] is False
    assert third["acceptance_status"] == "PASSED"
    assert third["duplicate_requests"] == 2

    persisted=duplicate_response(third)
    assert persisted["acceptance_status"] == "PASSED"
    assert persisted["live_model_verified"] is True
    assert persisted["gateway_request_count"] == 2
    assert persisted["artifact_count"] == 3
    assert persisted["duplicate_suppressed"] is True
    assert persisted["duplicate_requests"] == 2

    with engine.connect() as db:
        row=db.execute(
            text("""
              SELECT acceptance_status,phase,live_model_verified,
                     gateway_request_count,total_tokens,
                     estimated_cost_usd,artifact_count,
                     duplicate_requests,result_snapshot
              FROM live_acceptance_audits
              WHERE trigger_token_sha256=:trigger_hash
            """),
            {"trigger_hash":trigger_hash},
        ).mappings().one()

    assert row["acceptance_status"] == "PASSED"
    assert row["phase"] == "complete"
    assert row["live_model_verified"] is True
    assert row["gateway_request_count"] == 2
    assert row["total_tokens"] == 120
    assert float(row["estimated_cost_usd"]) == 0.001
    assert row["artifact_count"] == 3
    assert row["duplicate_requests"] == 2
    assert row["result_snapshot"]["source_tree_sha256"] == "b"*64
