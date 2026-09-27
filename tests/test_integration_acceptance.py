import hashlib

from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.workers.pipeline import run_pipeline

def _item(source_type: str, url: str, title: str, body: str):
    return {
        "source_type":source_type,
        "url":url,
        "title":title,
        "text":body,
        "fingerprint":hashlib.sha256(
            (source_type+"|"+url).encode("utf-8")
        ).hexdigest(),
    }

def test_full_v02_integration_acceptance():
    with engine.connect() as db:
        versions=[
            row[0] for row in db.execute(
                text("SELECT version FROM schema_migrations ORDER BY version")
            ).all()
        ]
    assert versions == [
        "001_initial.sql",
        "002_hunter_v02.sql",
        "003_cross_source_aggregator.sql",
        "004_evidence_quality.sql",
        "005_research_reports.sql",
        "006_audit_integrity.sql",
        "007_research_validation.sql",
        "008_build_proposals.sql",
    ]

    assert Redis.from_url(settings.redis_url).ping() is True

    items=[
        _item(
            "GITHUB_ISSUE",
            "https://github.com/acceptance/pricing-data/issues/101",
            "Competitor pricing tracker API",
            (
                "Developers need an alternative competitor pricing tracker dataset API. "
                "We currently pay $20 per month for manual competitor pricing data. "
                "The manual database workflow takes hours and is too expensive. "
                "We need automated pricing data API access and reliable price tracking."
            ),
        ),
        _item(
            "NEWS_ARTICLE",
            "https://acceptance.example.org/research/competitor-pricing-data",
            "Competitor price monitor dataset",
            (
                "Teams need a competitor alternative pricing dataset API and database. "
                "Our budget is $25 per month and we are willing to pay for better pricing data. "
                "Manual price tracking is slow and difficult. "
                "Existing pricing data plans cost $25 per month and teams need a better tracker."
            ),
        ),
    ]

    result=run_pipeline(acceptance_items=items)
    assert result["mode"] == "ACCEPTANCE"
    assert result["evidence"] == 2
    assert result["opportunities"] == 1
    assert result["research_reports"] >= 1
    assert result["research_validations"] >= 1
    assert result["build_proposals"] >= 1

    with engine.connect() as db:
        run=db.execute(text("""
          SELECT status,pages_discovered,evidence_created,opportunities_created
          FROM pipeline_runs
          WHERE id=CAST(:id AS uuid)
        """),{"id":result["run_id"]}).mappings().one()
        assert run["status"] == "SUCCESS"
        assert run["pages_discovered"] == 2
        assert run["evidence_created"] == 2
        assert run["opportunities_created"] == 1

        opportunities=db.execute(text("""
          SELECT id,status,score,independent_source_count,evidence_count,
                 evidence_quality_score,source_diversity_score,signal_strength_score,
                 evidence_gate_passed,research_validation_score,build_readiness
          FROM digital_asset_opportunities
        """)).mappings().all()
        assert len(opportunities) == 1
        opportunity=opportunities[0]

        assert opportunity["status"] == "CANDIDATE"
        assert float(opportunity["score"]) >= 75
        assert opportunity["independent_source_count"] == 2
        assert opportunity["evidence_count"] == 2
        assert float(opportunity["evidence_quality_score"]) >= 65
        assert float(opportunity["source_diversity_score"]) >= 60
        assert float(opportunity["signal_strength_score"]) >= 55
        assert opportunity["evidence_gate_passed"] is True
        assert float(opportunity["research_validation_score"]) >= 75
        assert opportunity["build_readiness"] == "BUILD_READY"

        report=db.execute(text("""
          SELECT report_status,observe_only,generator_version
          FROM research_reports
          WHERE opportunity_id=:id
        """),{"id":opportunity["id"]}).mappings().one()
        assert report["report_status"] == "GENERATED"
        assert report["observe_only"] is True
        assert report["generator_version"] == "research-v0.2-deterministic"

        validation=db.execute(text("""
          SELECT validation_status,buyer_status,competitors_status,pricing_status,
                 willingness_to_pay_status,market_gap_status,completeness_score,
                 validation_gate_passed,build_readiness,observe_only
          FROM research_validations
          WHERE opportunity_id=:id
        """),{"id":opportunity["id"]}).mappings().one()

        assert validation["validation_status"] == "CURRENT"
        assert validation["buyer_status"] == "VALIDATED"
        assert validation["competitors_status"] == "VALIDATED"
        assert validation["pricing_status"] == "VALIDATED"
        assert validation["willingness_to_pay_status"] == "VALIDATED"
        assert validation["market_gap_status"] == "VALIDATED"
        assert float(validation["completeness_score"]) == 100.0
        assert validation["validation_gate_passed"] is True
        assert validation["build_readiness"] == "BUILD_READY"
        assert validation["observe_only"] is True

        proposal=db.execute(text("""
          SELECT id,revision,proposal_status,requires_human_approval,
                 execution_enabled,source_fingerprint
          FROM build_proposals
          WHERE opportunity_id=:id
        """),{"id":opportunity["id"]}).mappings().one()

        assert proposal["revision"] == 1
        assert proposal["proposal_status"] == "PENDING_APPROVAL"
        assert proposal["requires_human_approval"] is True
        assert proposal["execution_enabled"] is False
        assert proposal["source_fingerprint"]

    client=TestClient(app)

    denied=client.post(
        f"/v1/build-proposals/{proposal['id']}/decision",
        json={
            "decision":"APPROVE",
            "reason":"acceptance approval",
            "actor":"ci-human",
        },
    )
    assert denied.status_code == 403

    approved=client.post(
        f"/v1/build-proposals/{proposal['id']}/decision",
        headers={"X-Approval-Key":settings.human_approval_key},
        json={
            "decision":"APPROVE",
            "reason":"acceptance approval",
            "actor":"ci-human",
        },
    )
    assert approved.status_code == 200
    approved_body=approved.json()
    assert approved_body["proposal_status"] == "APPROVED"
    assert approved_body["execution_enabled"] is False

    with engine.connect() as db:
        final=db.execute(text("""
          SELECT o.build_proposal_status,bp.proposal_status,
                 bp.requires_human_approval,bp.execution_enabled,
                 COUNT(d.id) AS decisions
          FROM digital_asset_opportunities o
          JOIN build_proposals bp ON bp.opportunity_id=o.id
          LEFT JOIN build_proposal_decisions d ON d.proposal_id=bp.id
          WHERE bp.id=:id
          GROUP BY o.build_proposal_status,bp.proposal_status,
                   bp.requires_human_approval,bp.execution_enabled
        """),{"id":proposal["id"]}).mappings().one()

        assert final["build_proposal_status"] == "APPROVED"
        assert final["proposal_status"] == "APPROVED"
        assert final["requires_human_approval"] is True
        assert final["execution_enabled"] is False
        assert final["decisions"] == 1
