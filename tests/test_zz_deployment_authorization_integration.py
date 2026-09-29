import hashlib

import pytest
from sqlalchemy import text

import app.deployment_authorization as deployment_auth
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
    with engine.connect() as db:
        before = {
            str(row[0])
            for row in db.execute(text("SELECT id FROM build_proposals")).all()
        }

    items = [
        _item(
            "GITHUB_ISSUE",
            f"https://github.com/ci/deployment-auth-{suffix}/issues/1",
            f"Deployment authorization pricing tracker {suffix}",
            (
                "Developers need an automated competitor pricing tracker API. "
                "We pay $20 per month for manual pricing data and want a better "
                "database workflow with reliable automated price monitoring."
            ),
        ),
        _item(
            "NEWS_ARTICLE",
            f"https://ci.example.invalid/deployment-auth-{suffix}",
            f"Pricing dataset demand {suffix}",
            (
                "Teams need a competitor pricing dataset API and database. "
                "The budget is $25 per month and teams are willing to pay for "
                "automated price tracking because manual tracking is too slow."
            ),
        ),
    ]

    result = run_pipeline(acceptance_items=items)
    assert result["mode"] == "ACCEPTANCE"

    with engine.connect() as db:
        rows = db.execute(text("""
          SELECT id
          FROM build_proposals
          ORDER BY generated_at,id
        """)).all()

    created = [str(row[0]) for row in rows if str(row[0]) not in before]
    assert len(created) == 1, created
    return created[0]


def _approved_release_fixture(monkeypatch, suffix: str, integrity: dict):
    proposal_id = _new_proposal_id(suffix)

    proposal_decision = decide_build_proposal(
        proposal_id,
        decision="APPROVE",
        reason="CI deployment authorization acceptance",
        actor="ci-human",
    )
    assert proposal_decision["proposal_status"] == "APPROVED"
    assert proposal_decision["execution_enabled"] is False

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

    return release, plan


def test_deployment_authorization_three_path_acceptance(monkeypatch):
    integrity_a = _verified_integrity("1")
    release_a, plan_a = _approved_release_fixture(
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

    # 3) Independent REJECT path because terminal authorization is immutable.
    integrity_b = _verified_integrity("6")
    release_b, plan_b = _approved_release_fixture(
        monkeypatch,
        "reject",
        integrity_b,
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
