import hashlib

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.migrate import migration_files
from app.release_gate import decide_release_candidate, ensure_release_candidate
from app.sandbox_execution import create_sandbox_request, execute_sandbox_request
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
    assert versions == migration_files()
    assert versions[-1] == "039_shrimp_animation_episode_package.sql"

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

    sandbox_request=create_sandbox_request(
        proposal["id"],
        requested_by="ci-human",
        executor_kind="ACCEPTANCE",
    )
    assert sandbox_request["request_status"] == "POLICY_PASSED"
    assert sandbox_request["policy"]["network"] == "DENY"
    assert sandbox_request["policy"]["docker_network"] == "none"
    assert sandbox_request["policy"]["filesystem_scope"] == "WORKSPACE_ONLY"
    assert sandbox_request["policy"]["deployment"] == "DENY"
    assert sandbox_request["policy"]["external_side_effects"] == "DENY"

    sandbox_result=execute_sandbox_request(sandbox_request["request_id"])
    if sandbox_result["request_status"] != "ARTIFACT_READY":
        with engine.connect() as db:
            debug=db.execute(text("""
              SELECT sbr.error,sr.stdout,sr.stderr
              FROM sandbox_build_requests sbr
              LEFT JOIN sandbox_runs sr ON sr.request_id=sbr.id
              WHERE sbr.id=CAST(:id AS uuid)
            """),{"id":sandbox_request["request_id"]}).mappings().one()
        raise AssertionError(f"sandbox failed: {dict(debug)}")
    assert sandbox_result["request_status"] == "ARTIFACT_READY"
    assert sandbox_result["container_network"] == "none"
    assert sandbox_result["test_passed"] is True
    assert sandbox_result["artifact_count"] >= 2

    with engine.connect() as db:
        sandbox=db.execute(text("""
          SELECT sbr.request_status,sbr.network_policy,sbr.workspace_policy,
                 sbr.external_side_effects,sr.id AS run_id,
                 sr.container_network,sr.exit_code,
                 COUNT(DISTINCT sa.id) AS artifacts,
                 COUNT(DISTINCT str.id) FILTER (WHERE str.passed=true) AS passed_tests
          FROM sandbox_build_requests sbr
          JOIN sandbox_runs sr ON sr.request_id=sbr.id
          LEFT JOIN sandbox_artifacts sa ON sa.run_id=sr.id
          LEFT JOIN sandbox_test_results str ON str.run_id=sr.id
          WHERE sbr.id=CAST(:id AS uuid)
          GROUP BY sbr.id,sr.id
        """),{"id":sandbox_request["request_id"]}).mappings().one()

        assert sandbox["request_status"] == "ARTIFACT_READY"
        assert sandbox["network_policy"] == "DENY"
        assert sandbox["workspace_policy"] == "ISOLATED_RW"
        assert sandbox["external_side_effects"] == "DENY"
        assert sandbox["container_network"] == "none"
        assert sandbox["exit_code"] == 0
        assert sandbox["artifacts"] >= 2
        assert sandbox["passed_tests"] == 1


def test_openhands_real_cli_adapter_from_approved_proposal():
    with engine.connect() as db:
        proposal=db.execute(text("""
          SELECT id
          FROM build_proposals
          WHERE proposal_status='APPROVED'
          ORDER BY updated_at DESC
          LIMIT 1
        """)).scalar_one()

    request=create_sandbox_request(
        proposal,
        requested_by="ci-human",
        executor_kind="OPENHANDS",
    )
    assert request["request_status"] == "POLICY_PASSED"
    assert request["policy"]["network"] == "INTERNAL_GATEWAY_ONLY"
    assert request["policy"]["docker_network"] == "internal-gateway"
    assert request["policy"]["inner_runtime"] == "process"
    assert request["policy"]["host_docker_socket"] == "DENY"

    result=execute_sandbox_request(request["request_id"])
    if result["request_status"] != "ARTIFACT_READY":
        with engine.connect() as db:
            debug=db.execute(text("""
              SELECT sbr.error,sr.stdout,sr.stderr,oe.trace_jsonl
              FROM sandbox_build_requests sbr
              LEFT JOIN sandbox_runs sr ON sr.request_id=sbr.id
              LEFT JOIN openhands_executions oe ON oe.request_id=sbr.id
              WHERE sbr.id=CAST(:id AS uuid)
            """),{"id":request["request_id"]}).mappings().one()
        raise AssertionError(f"OpenHands adapter failed: {dict(debug)}")

    assert result["executor_kind"] == "OPENHANDS"
    assert result["request_status"] == "ARTIFACT_READY"
    assert result["cli_version"] == settings.openhands_cli_version
    assert result["gateway_mode"] == "MOCK"
    assert result["container_network"] == "internal-gateway"
    assert result["test_passed"] is True
    assert result["artifact_count"] >= 2

    with engine.connect() as db:
        row=db.execute(text("""
          SELECT sbr.request_status,sbr.network_policy,sbr.external_side_effects,
                 sr.container_network,sr.exit_code,
                 oe.cli_version,oe.model_name,oe.inner_runtime,
                 oe.network_policy AS oh_network_policy,oe.gateway_mode,
                 oe.exit_code AS openhands_exit_code,oe.trace_jsonl,
                 bp.execution_enabled,
                 COUNT(DISTINCT sa.id) AS artifacts,
                 COUNT(DISTINCT str.id) FILTER (WHERE str.passed=true) AS passed_tests
          FROM sandbox_build_requests sbr
          JOIN build_proposals bp ON bp.id=sbr.proposal_id
          JOIN sandbox_runs sr ON sr.request_id=sbr.id
          JOIN openhands_executions oe ON oe.request_id=sbr.id
          LEFT JOIN sandbox_artifacts sa ON sa.run_id=sr.id
          LEFT JOIN sandbox_test_results str ON str.run_id=sr.id
          WHERE sbr.id=CAST(:id AS uuid)
          GROUP BY sbr.id,sr.id,oe.id,bp.id
        """),{"id":request["request_id"]}).mappings().one()

    assert row["request_status"] == "ARTIFACT_READY"
    assert row["network_policy"] == "INTERNAL_GATEWAY_ONLY"
    assert row["external_side_effects"] == "DENY"
    assert row["container_network"] == "internal-gateway"
    assert row["exit_code"] == 0
    assert row["cli_version"] == "1.16.0"
    assert row["model_name"] == "openai/mock-test-model"
    assert row["inner_runtime"] == "process"
    assert row["oh_network_policy"] == "INTERNAL_GATEWAY_ONLY"
    assert row["gateway_mode"] == "MOCK"
    assert row["openhands_exit_code"] == 0
    assert "OPENHANDS" in row["trace_jsonl"].upper()
    assert row["execution_enabled"] is False
    assert row["artifacts"] >= 2
    assert row["passed_tests"] == 1

    release=ensure_release_candidate(request["request_id"])
    assert release["release_status"] == "WAITING_LIVE_VALIDATION"
    assert release["live_validation_verified"] is False
    assert release["deployment_enabled"] is False
    assert len(release["artifact_manifest_sha256"]) == 64
    assert len(release["source_tree_sha256"]) == 64
    assert len(release["review_package_sha256"]) == 64
    assert release["review_snapshot_complete"] is True

    with engine.connect() as db:
        review=db.execute(text("""
          SELECT package_status,content_snapshot_complete,
                 artifact_manifest,artifact_diff,dependency_inventory,
                 sbom,test_report,risk_summary,
                 source_tree_sha256,package_sha256,generator_version
          FROM release_review_packages
          WHERE id=CAST(:id AS uuid)
        """),{"id":release["review_package_id"]}).mappings().one()
        assert review["package_status"] == "GENERATED"
        assert review["content_snapshot_complete"] is True
        assert len(review["artifact_manifest"]) >= 2
        assert all(x["status"] == "ADDED" for x in review["artifact_diff"])
        assert review["dependency_inventory"] == []
        assert review["sbom"]["bomFormat"] == "CycloneDX"
        assert review["test_report"]["all_passed"] is True
        assert review["test_report"]["passed"] >= 1
        assert review["risk_summary"]["risk_level"] == "LOW"
        assert len(review["source_tree_sha256"]) == 64
        assert len(review["package_sha256"]) == 64
        assert review["generator_version"] == "release-review-v0.3-deterministic"

    with pytest.raises(Exception):
        with engine.begin() as db:
            db.execute(text("""
              UPDATE release_review_packages
              SET package_sha256=repeat('0',64)
              WHERE id=CAST(:id AS uuid)
            """),{"id":release["review_package_id"]})

    with engine.connect() as db:
        artifact_id=db.execute(text("""
          SELECT sa.id
          FROM sandbox_artifacts sa
          WHERE sa.run_id=CAST(:run_id AS uuid)
          ORDER BY sa.relative_path
          LIMIT 1
        """),{"run_id":release["run_id"]}).scalar_one()

    with pytest.raises(Exception):
        with engine.begin() as db:
            db.execute(text("""
              UPDATE sandbox_artifact_contents
              SET content_sha256=repeat('0',64)
              WHERE artifact_id=:artifact_id
            """),{"artifact_id":artifact_id})

    client=TestClient(app)

    review_page=client.get("/review")
    assert review_page.status_code == 200
    assert "Human Review Workspace" in review_page.text
    assert "frame-ancestors 'none'" in review_page.headers["content-security-policy"]
    assert review_page.headers["cache-control"] == "no-store"

    deep_link=client.get(f"/review/{release['release_candidate_id']}")
    assert deep_link.status_code == 200

    workspace=client.get(
        f"/v1/review-workspace/{release['release_candidate_id']}"
    )
    assert workspace.status_code == 200
    workspace_body=workspace.json()
    assert workspace_body["release_status"] == "WAITING_LIVE_VALIDATION"
    assert workspace_body["live_validation_verified"] is False
    assert workspace_body["deployment_enabled"] is False
    assert workspace_body["can_approve"] is False
    assert workspace_body["can_reject"] is True
    assert workspace_body["package_sha256"] == release["review_package_sha256"]
    assert workspace_body["ui_safety"]["auto_deploy"] is False
    assert workspace_body["ui_safety"]["release_key_persisted_in_browser"] is False

    with pytest.raises(
        RuntimeError,
        match="Controlled Live LLM Acceptance has not passed",
    ):
        decide_release_candidate(
            release["release_candidate_id"],
            decision="APPROVE",
            reason="must remain blocked without live validation",
            actor="ci-human",
        )

    with engine.begin() as db:
        candidate=db.execute(text("""
          SELECT release_status,live_validation_required,
                 live_validation_verified,deployment_enabled,
                 jsonb_array_length(artifact_manifest) AS artifact_count,
                 (test_summary->>'passed')::int AS passed_tests
          FROM release_candidates
          WHERE id=CAST(:id AS uuid)
        """),{"id":release["release_candidate_id"]}).mappings().one()
        assert candidate["release_status"] == "WAITING_LIVE_VALIDATION"
        assert candidate["live_validation_required"] is True
        assert candidate["live_validation_verified"] is False
        assert candidate["deployment_enabled"] is False
        assert candidate["artifact_count"] >= 2
        assert candidate["passed_tests"] >= 1

    with pytest.raises(Exception):
        with engine.begin() as db:
            db.execute(text("""
              UPDATE release_candidates
              SET release_status='READY_FOR_REVIEW'
              WHERE id=CAST(:id AS uuid)
            """),{"id":release["release_candidate_id"]})
