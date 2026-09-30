import hashlib

import pytest
from sqlalchemy import text

import app.deployment_authorization as deployment_auth
import app.production_release as production_release
import app.release_gate as release_gate
from app.build_proposals import decide_build_proposal
from app.db import engine
from app.release_gate import decide_release_candidate, ensure_release_candidate
from app.sandbox_execution import create_sandbox_request, execute_sandbox_request
from app.workers.pipeline import run_pipeline


def _item(source_type: str, url: str, title: str, body: str):
    return {
        "source_type": source_type,
        "url": url,
        "title": title,
        "text": body,
        "fingerprint": hashlib.sha256(
            (source_type + "|" + url).encode("utf-8")
        ).hexdigest(),
    }


def _verified_integrity(seed: str = "1"):
    chars = {
        "acceptance": seed,
        "evidence": "2",
        "audit_chain": "3",
        "manifest": "4",
        "head": "5",
    }
    return {
        "allowed": True,
        "integrity_status": "VERIFIED",
        "blocking_reasons": [],
        "acceptance_provenance_tree_sha256": chars["acceptance"] * 64,
        "audit_id": "ci-preview-acceptance-" + seed,
        "audit_evidence_sha256": chars["evidence"] * 64,
        "audit_chain_sha256": chars["audit_chain"] * 64,
        "manifest_root_sha256": chars["manifest"] * 64,
        "chain_head_sha256": chars["head"] * 64,
        "source_commit": "a" * 40,
        "deployment_source_commit": "a" * 40,
        "vercel_deployment_id": "dpl_ci_preview_" + seed,
    }


def _new_proposal_id(suffix: str):
    if suffix == "authorize":
        subject = "commercial drone roof damage inspection"
        github_url = "https://github.com/ci/drone-roof-damage-dataset/issues/1"
        news_url = "https://ci.example.invalid/drone-roof-damage-dataset"
    else:
        subject = "restaurant allergen menu normalization"
        github_url = "https://github.com/ci/allergen-menu-dataset/issues/1"
        news_url = "https://ci.example.invalid/allergen-menu-dataset"

    items = [
        _item(
            "GITHUB_ISSUE",
            github_url,
            f"{subject} dataset API demand",
            (
                f"Developers need an automated {subject} dataset API and database. "
                "We currently pay $39 per month for manual data collection and "
                "want reliable automation because the manual workflow takes hours."
            ),
        ),
        _item(
            "NEWS_ARTICLE",
            news_url,
            f"{subject} commercial data service",
            (
                f"Teams need a {subject} dataset API as an alternative to manual work. "
                "The budget is $45 per month and buyers are willing to pay for "
                "automated structured data, monitoring, and reliable database access."
            ),
        ),
    ]

    result = run_pipeline(acceptance_items=items)
    assert result["mode"] == "ACCEPTANCE"

    with engine.connect() as db:
        row = db.execute(text("""
          SELECT id,proposal_status,revision
          FROM build_proposals
          WHERE proposal_status='PENDING_APPROVAL'
          ORDER BY updated_at DESC,id DESC
          LIMIT 1
        """)).mappings().one_or_none()

    assert row is not None
    assert row["proposal_status"] == "PENDING_APPROVAL"
    assert int(row["revision"]) >= 1
    return str(row["id"])


def _approved_release_fixture(
    monkeypatch,
    suffix: str,
    integrity: dict,
    *,
    proposal_id: str | None = None,
):
    if proposal_id is None:
        proposal_id = _new_proposal_id(suffix)
        proposal_decision = decide_build_proposal(
            proposal_id,
            decision="APPROVE",
            reason="CI deployment authorization acceptance",
            actor="ci-human",
        )
        assert proposal_decision["proposal_status"] == "APPROVED"
        assert proposal_decision["execution_enabled"] is False
    else:
        with engine.connect() as db:
            proposal = db.execute(text("""
              SELECT proposal_status,execution_enabled
              FROM build_proposals
              WHERE id=CAST(:id AS uuid)
            """), {"id": proposal_id}).mappings().one()
        assert proposal["proposal_status"] == "APPROVED"
        assert proposal["execution_enabled"] is False

    request = create_sandbox_request(
        proposal_id,
        requested_by="ci-deployment-authorization",
        executor_kind="ACCEPTANCE",
    )
    sandbox = execute_sandbox_request(request["request_id"])
    assert sandbox["request_status"] == "ARTIFACT_READY"
    assert sandbox["test_passed"] is True

    # Controlled CI-only Live Acceptance marker. No external model/network call.
    task_sha = hashlib.sha256(
        ("ci-deployment-authorization|" + suffix).encode("utf-8")
    ).hexdigest()
    with engine.begin() as db:
        db.execute(text("""
          INSERT INTO openhands_executions(
            request_id,run_id,cli_version,model_name,inner_runtime,
            network_policy,gateway_mode,task_sha256,exit_code,trace_jsonl,
            budget_status,gateway_request_count,prompt_tokens,
            completion_tokens,total_tokens,estimated_cost_usd,
            live_model_verified,finished_at)
          VALUES(
            CAST(:request_id AS uuid),CAST(:run_id AS uuid),
            'CI-CONTROLLED','ci-no-external-model','process',
            'INTERNAL_GATEWAY_ONLY','PROXY',:task_sha,0,
            '{"event":"ci_controlled_no_external_model"}',
            'WITHIN_BUDGET',0,0,0,0,0,true,now())
        """), {
            "request_id": request["request_id"],
            "run_id": sandbox["run_id"],
            "task_sha": task_sha,
        })

    release = ensure_release_candidate(request["request_id"])
    assert release["release_status"] == "READY_FOR_REVIEW"
    assert release["live_validation_verified"] is True
    assert release["review_snapshot_complete"] is True
    assert release["deployment_enabled"] is False

    monkeypatch.setattr(
        release_gate,
        "evaluate_release_integrity",
        lambda *_args, **_kwargs: dict(integrity),
    )

    release_decision = decide_release_candidate(
        release["release_candidate_id"],
        decision="APPROVE",
        reason="CI controlled release approval for deployment authorization",
        actor="ci-human-release",
        review_package_sha256=release["review_package_sha256"],
    )
    assert release_decision["release_status"] == "RELEASE_APPROVED"
    assert release_decision["deployment_enabled"] is False

    monkeypatch.setattr(
        deployment_auth,
        "evaluate_release_integrity",
        lambda *_args, **_kwargs: dict(integrity),
    )

    plan = deployment_auth.create_deployment_plan(
        release["release_candidate_id"],
        target_project_id="prj_ci_preview_only",
        target_team_id="team_ci_preview_only",
        actor="ci-deployment-planner",
    )
    assert plan["plan_status"] == "PENDING_AUTHORIZATION"
    assert plan["execution_enabled"] is False
    assert len(plan["plan_sha256"]) == 64

    return release, plan, proposal_id


def test_deployment_authorization_three_path_acceptance(monkeypatch):
    integrity_a = _verified_integrity("1")
    release_a, plan_a, proposal_id = _approved_release_fixture(
        monkeypatch,
        "authorize",
        integrity_a,
    )

    # 1) Drift path: same plan, changed current provenance -> persisted block.
    drifted = dict(integrity_a)
    drifted["manifest_root_sha256"] = "9" * 64
    monkeypatch.setattr(
        deployment_auth,
        "evaluate_release_integrity",
        lambda *_args, **_kwargs: dict(drifted),
    )

    with pytest.raises(RuntimeError, match="manifest_root_sha256_drift"):
        deployment_auth.decide_deployment_authorization(
            plan_a["id"],
            decision="AUTHORIZE",
            reason="CI controlled drift probe",
            actor="ci-deployment-reviewer",
            plan_sha256=plan_a["plan_sha256"],
        )

    after_block = deployment_auth.get_deployment_plan_for_candidate(
        release_a["release_candidate_id"]
    )
    assert after_block["plan_status"] == "PENDING_AUTHORIZATION"
    assert after_block["execution_enabled"] is False
    assert len(after_block["blocks"]) == 1
    assert len(after_block["decisions"]) == 0
    assert "manifest_root_sha256_drift" in after_block["blocks"][0][
        "blocking_reasons"
    ]

    # 2) Valid AUTHORIZE path: restore exact provenance.
    monkeypatch.setattr(
        deployment_auth,
        "evaluate_release_integrity",
        lambda *_args, **_kwargs: dict(integrity_a),
    )
    authorized = deployment_auth.decide_deployment_authorization(
        plan_a["id"],
        decision="AUTHORIZE",
        reason="CI controlled exact provenance authorization",
        actor="ci-deployment-reviewer",
        plan_sha256=plan_a["plan_sha256"],
    )
    assert authorized["plan_status"] == "AUTHORIZED_FOR_DEPLOYMENT"
    assert authorized["execution_enabled"] is False
    assert authorized["production_deployment_executed"] is False

    after_authorize = deployment_auth.get_deployment_plan_for_candidate(
        release_a["release_candidate_id"]
    )
    assert after_authorize["plan_status"] == "AUTHORIZED_FOR_DEPLOYMENT"
    assert after_authorize["execution_enabled"] is False
    assert len(after_authorize["blocks"]) == 1
    assert len(after_authorize["decisions"]) == 1
    assert after_authorize["decisions"][0]["decision"] == "AUTHORIZE"

    with engine.connect() as db:
        candidate_a = db.execute(text("""
          SELECT deployment_enabled
          FROM release_candidates
          WHERE id=CAST(:id AS uuid)
        """), {"id": release_a["release_candidate_id"]}).mappings().one()
    assert candidate_a["deployment_enabled"] is False

    # Controlled Production Release Executor Step 1:
    # authorized immutable plan -> exact execution snapshot -> MOCK prepare only.
    execution = production_release.create_production_release_execution(
        plan_a["id"],
        actor="ci-production-snapshot",
    )
    assert execution["execution_status"] == "SNAPSHOT_CREATED"
    assert execution["executor_adapter"] == "MOCK"
    assert execution["production_execution_enabled"] is False
    assert execution["automatic_execution"] is False
    assert execution["automatic_promotion"] is False
    assert len(execution["execution_bundle_sha256"]) == 64
    assert len(execution["execution_sha256"]) == 64

    duplicate = production_release.create_production_release_execution(
        plan_a["id"],
        actor="ci-production-snapshot-retry",
    )
    assert duplicate["id"] == execution["id"]
    assert duplicate["execution_sha256"] == execution["execution_sha256"]

    prepared = production_release.prepare_mock_candidate(
        execution["id"],
        actor="ci-mock-production-preparer",
    )
    assert prepared["execution_status"] == "READY_FOR_PROMOTION"
    assert prepared["candidate_vercel_deployment_id"].startswith("mock_dpl_")
    assert prepared["candidate_vercel_url"].endswith(".mock.invalid")
    assert prepared["production_execution_enabled"] is False
    assert prepared["automatic_execution"] is False
    assert prepared["automatic_promotion"] is False
    assert prepared["previous_production_deployment_id"] is None
    assert prepared["production_vercel_deployment_id"] is None

    prepared_duplicate = production_release.prepare_mock_candidate(
        execution["id"],
        actor="ci-mock-production-preparer-retry",
    )
    assert prepared_duplicate["id"] == execution["id"]
    assert (
        prepared_duplicate["candidate_vercel_deployment_id"]
        == prepared["candidate_vercel_deployment_id"]
    )

    execution_detail = production_release.get_production_release_execution(
        execution["id"]
    )
    assert execution_detail["execution_status"] == "READY_FOR_PROMOTION"
    assert execution_detail["decisions"] == []
    event_types = [item["event_type"] for item in execution_detail["events"]]
    assert event_types == [
        "EXECUTION_SNAPSHOT_CREATED",
        "PREPARE_REQUESTED",
        "CANDIDATE_CREATED",
        "CANDIDATE_READY",
        "CANDIDATE_VERIFIED",
    ]
    artifact_bundle = execution_detail["execution_bundle"]
    assert artifact_bundle["schema_version"] == "production-execution-bundle-v1"
    assert artifact_bundle["artifacts"]
    assert all(
        item["content_base64"]
        for item in artifact_bundle["artifacts"]
    )

    with engine.connect() as db:
        unsafe_execution = db.execute(text("""
          SELECT COUNT(*)
          FROM production_release_executions
          WHERE production_execution_enabled=true
             OR automatic_execution=true
             OR automatic_promotion=true
        """)).scalar_one()
    assert unsafe_execution == 0

    # 3) Independent REJECT path because terminal authorization is immutable.
    integrity_b = _verified_integrity("6")
    release_b, plan_b, _ = _approved_release_fixture(
        monkeypatch,
        "reject",
        integrity_b,
        proposal_id=proposal_id,
    )
    rejected = deployment_auth.decide_deployment_authorization(
        plan_b["id"],
        decision="REJECT",
        reason="CI controlled independent reject path",
        actor="ci-deployment-reviewer",
        plan_sha256=plan_b["plan_sha256"],
    )
    assert rejected["plan_status"] == "DEPLOYMENT_REJECTED"
    assert rejected["execution_enabled"] is False
    assert rejected["production_deployment_executed"] is False

    after_reject = deployment_auth.get_deployment_plan_for_candidate(
        release_b["release_candidate_id"]
    )
    assert after_reject["plan_status"] == "DEPLOYMENT_REJECTED"
    assert after_reject["execution_enabled"] is False
    assert len(after_reject["blocks"]) == 0
    assert len(after_reject["decisions"]) == 1
    assert after_reject["decisions"][0]["decision"] == "REJECT"

    with engine.connect() as db:
        candidate_b = db.execute(text("""
          SELECT deployment_enabled
          FROM release_candidates
          WHERE id=CAST(:id AS uuid)
        """), {"id": release_b["release_candidate_id"]}).mappings().one()
    assert candidate_b["deployment_enabled"] is False

    with pytest.raises(LookupError, match="Authorized Deployment Plan not found"):
        production_release.create_production_release_execution(
            plan_b["id"],
            actor="ci-rejected-plan-must-not-execute",
        )
