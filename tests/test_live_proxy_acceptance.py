import hashlib
import os

import pytest
from sqlalchemy import text

from app.build_proposals import decide_build_proposal
from app.config import settings
from app.db import engine
from app.sandbox_execution import create_sandbox_request, execute_sandbox_request
from app.workers.pipeline import run_pipeline

pytestmark=pytest.mark.skipif(
    os.environ.get("LIVE_PROXY_ACCEPTANCE")!="1",
    reason="live controlled proxy acceptance is opt-in",
)

def _item(source_type,url,title,body):
    return {
        "source_type":source_type,
        "url":url,
        "title":title,
        "text":body,
        "fingerprint":hashlib.sha256((source_type+"|"+url).encode("utf-8")).hexdigest(),
    }

def test_live_controlled_proxy_build():
    assert settings.openhands_gateway_mode.strip().upper()=="PROXY"
    assert settings.openhands_llm_api_key.strip()
    assert settings.openhands_model in settings.openhands_allowed_model_list

    result=run_pipeline(acceptance_items=[
        _item(
            "GITHUB_ISSUE",
            "https://github.com/acceptance/live-proxy/issues/1",
            "Competitor pricing tracker API",
            "Developers need an automated competitor pricing dataset API. We pay $20 per month for manual tracking and need a reliable alternative.",
        ),
        _item(
            "NEWS_ARTICLE",
            "https://acceptance.example.org/live-proxy/pricing",
            "Competitor price monitor dataset",
            "Teams are willing to pay $25 per month for better automated pricing data because manual tracking is slow and existing alternatives are expensive.",
        ),
    ])
    assert result["build_proposals"]>=1

    with engine.connect() as db:
        proposal_id=db.execute(text("""
          SELECT id FROM build_proposals
          WHERE proposal_status='PENDING_APPROVAL'
          ORDER BY updated_at DESC LIMIT 1
        """)).scalar_one()

    approved=decide_build_proposal(
        proposal_id,
        decision="APPROVE",
        reason="manual live controlled proxy acceptance",
        actor="github-actions-human-dispatch",
    )
    assert approved["proposal_status"]=="APPROVED"
    assert approved["execution_enabled"] is False

    request=create_sandbox_request(
        proposal_id,
        requested_by="github-actions-human-dispatch",
        executor_kind="OPENHANDS",
    )
    result=execute_sandbox_request(request["request_id"])
    assert result["request_status"]=="ARTIFACT_READY",result
    assert result["test_passed"] is True
    assert result["artifact_count"]>=1
    assert result["budget_status"]=="WITHIN_BUDGET"

    with engine.connect() as db:
        row=db.execute(text("""
          SELECT oe.live_model_verified,oe.budget_status,oe.gateway_request_count,
                 oe.total_tokens,oe.estimated_cost_usd,oe.blocked_reason,
                 bp.execution_enabled,sbr.request_status
          FROM openhands_executions oe
          JOIN sandbox_build_requests sbr ON sbr.id=oe.request_id
          JOIN build_proposals bp ON bp.id=sbr.proposal_id
          WHERE oe.request_id=CAST(:id AS uuid)
        """),{"id":request["request_id"]}).mappings().one()

    assert row["live_model_verified"] is True,row
    assert row["budget_status"]=="WITHIN_BUDGET",row
    assert row["gateway_request_count"]<=settings.openhands_max_requests,row
    assert row["total_tokens"]<=settings.openhands_max_total_tokens,row
    assert float(row["estimated_cost_usd"])<=settings.openhands_max_cost_usd,row
    assert row["blocked_reason"] is None,row
    assert row["execution_enabled"] is False,row
    assert row["request_status"]=="ARTIFACT_READY",row
