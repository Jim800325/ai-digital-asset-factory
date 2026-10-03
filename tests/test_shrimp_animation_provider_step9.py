from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.human_review import (
    REQUIRED_REVIEW_CHECKLIST,
    decide_shrimp_release,
    get_shrimp_review_workspace,
)
from app.providers.animation.shrimp.provider import (
    get_shrimp_animation_job,
    update_shrimp_content_brief,
)
from app.providers.animation.shrimp.publishing_authorization import (
    get_publish_plan,
    list_publish_plans,
    list_publish_targets,
)

from tests.test_shrimp_animation_provider_step4 import (
    _cleanup_registry,
    _create_proposal,
    _seed_registry,
)
from tests.test_shrimp_animation_provider_step8 import (
    _build_review_ready_job,
)


def _approve_step8(job_id) -> dict:
    workspace = get_shrimp_review_workspace(job_id)
    result = decide_shrimp_release(
        job_id,
        decision="APPROVE",
        reason="Step 9 requires an explicit Step 8 approval.",
        actor="ci-shrimp-step9-reviewer",
        episode_bundle_sha256=workspace["episode_bundle_sha256"],
        release_review_package_sha256=workspace[
            "release_review_package_sha256"
        ],
        confirmed_checklist=list(REQUIRED_REVIEW_CHECKLIST),
    )
    assert result["review_status"] == "RELEASE_APPROVED"
    return get_shrimp_review_workspace(job_id)


def _plan_payload(target_key: str, title: str) -> dict:
    return {
        "target_key": target_key,
        "publish_metadata": {
            "title": title,
            "description": "CI-only controlled Publisher Plan dry-run.",
            "tags": ["shrimp", "animation", "ci"],
            "category": "animation-test",
            "visibility": "DRAFT",
        },
        "actor": "ci-shrimp-step9-plan",
    }


def test_step9_publish_target_plan_dry_run_authorize_reject_and_stale(
    monkeypatch,
):
    opportunity_ids: list[str] = []
    try:
        register_shrimp_animation_provider()
        reusable_media = _seed_registry()
        monkeypatch.setattr(
            settings,
            "shrimp_human_review_key",
            "ci-step8-human-review-key",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_authorization_key",
            "ci-step9-publish-key",
        )

        client = TestClient(app)

        wrong_target_key = client.post(
            "/v1/shrimp-animation/publish-targets",
            headers={"X-Shrimp-Publish-Key": "wrong-key"},
            json={
                "target_key": "ci-bilibili",
                "platform": "BILIBILI",
                "display_name": "CI Bilibili",
                "metadata_constraints": {},
                "actor": "ci",
            },
        )
        assert wrong_target_key.status_code == 403

        bilibili_target = client.post(
            "/v1/shrimp-animation/publish-targets",
            headers={"X-Shrimp-Publish-Key": "ci-step9-publish-key"},
            json={
                "target_key": "ci-bilibili",
                "platform": "BILIBILI",
                "display_name": "CI Bilibili Dry Run",
                "account_reference": "ci-account-reference",
                "metadata_constraints": {
                    "max_title_chars": 80,
                    "max_description_chars": 2000,
                    "max_tags": 12,
                    "max_tag_chars": 40,
                    "allowed_visibilities": ["DRAFT", "PRIVATE", "PUBLIC"],
                    "require_category": True,
                },
                "actor": "ci-shrimp-step9-target",
            },
        )
        assert bilibili_target.status_code == 201
        bilibili = bilibili_target.json()
        assert bilibili["platform"] == "BILIBILI"
        assert bilibili["target_status"] == "ACTIVE"
        assert bilibili["execution_enabled"] is False
        assert bilibili["external_publish_enabled"] is False

        youtube_target = client.post(
            "/v1/shrimp-animation/publish-targets",
            headers={"X-Shrimp-Publish-Key": "ci-step9-publish-key"},
            json={
                "target_key": "ci-youtube",
                "platform": "YOUTUBE",
                "display_name": "CI YouTube Dry Run",
                "metadata_constraints": {},
                "actor": "ci-shrimp-step9-target",
            },
        )
        assert youtube_target.status_code == 201

        targets = list_publish_targets(active_only=True)
        assert {item["target_key"] for item in targets} >= {
            "ci-bilibili",
            "ci-youtube",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            opportunity_id, proposal_id = _create_proposal()
            opportunity_ids.append(str(opportunity_id))
            approve_job, approve_brief, _ = _build_review_ready_job(
                proposal_id,
                reusable_media,
                temp_dir=temp_dir,
                requested_by="ci-shrimp-step9-authorize",
            )
            approved_workspace = _approve_step8(approve_job)
            assert approved_workspace["review_status"] == "RELEASE_APPROVED"

            wrong_plan_key = client.post(
                f"/v1/shrimp-animation/jobs/{approve_job}/publish-plans",
                headers={"X-Shrimp-Publish-Key": "wrong-key"},
                json=_plan_payload("ci-bilibili", "CI Episode"),
            )
            assert wrong_plan_key.status_code == 403

            invalid_metadata = client.post(
                f"/v1/shrimp-animation/jobs/{approve_job}/publish-plans",
                headers={
                    "X-Shrimp-Publish-Key": "ci-step9-publish-key",
                },
                json={
                    "target_key": "ci-bilibili",
                    "publish_metadata": {
                        "title": "CI Episode",
                        "description": "valid",
                        "tags": [],
                        "category": None,
                        "visibility": "DRAFT",
                    },
                    "actor": "ci",
                },
            )
            assert invalid_metadata.status_code == 422

            plan_response = client.post(
                f"/v1/shrimp-animation/jobs/{approve_job}/publish-plans",
                headers={
                    "X-Shrimp-Publish-Key": "ci-step9-publish-key",
                },
                json=_plan_payload(
                    "ci-bilibili",
                    "Step 9 Controlled Publisher CI Episode",
                ),
            )
            assert plan_response.status_code == 201
            plan = plan_response.json()
            assert plan["plan_status"] == "PENDING_AUTHORIZATION"
            assert plan["dry_run_status"] == "VERIFIED"
            assert plan["dry_run_verified"] is True
            assert plan["execution_enabled"] is False
            assert plan["publish_performed"] is False
            assert plan["network_request_count"] == 0
            assert plan["credential_access_count"] == 0
            assert plan["external_write_count"] == 0
            assert plan["next_stage"] == "PUBLISH_AUTHORIZATION"
            assert len(plan["plan_sha256"]) == 64
            assert len(plan["dry_run_sha256"]) == 64

            stored = get_publish_plan(plan["id"])
            dry = stored["dry_run_snapshot"]
            assert dry["network_request_count"] == 0
            assert dry["credential_access_count"] == 0
            assert dry["external_write_count"] == 0
            assert dry["execution_enabled"] is False
            assert dry["external_publish_enabled"] is False
            assert dry["publish_performed"] is False
            assert dry["external_side_effects"] == "DENY"
            assert all(item["passed"] for item in dry["checks"])
            assert stored["review_decision_sha256"] == (
                approved_workspace["decisions"][0]["decision_sha256"]
            )
            assert stored["episode_bundle_sha256"] == (
                approved_workspace["episode_bundle_sha256"]
            )
            assert stored["release_review_package_sha256"] == (
                approved_workspace["release_review_package_sha256"]
            )

            plans = list_publish_plans(approve_job)
            assert len(plans) == 1
            assert plans[0]["id"] == plan["id"]

            wrong_auth_key = client.post(
                f"/v1/shrimp-animation/publish-plans/{plan['id']}/decision",
                headers={"X-Shrimp-Publish-Key": "wrong-key"},
                json={
                    "decision": "AUTHORIZE",
                    "reason": "wrong key must fail",
                    "actor": "ci",
                    "plan_sha256": plan["plan_sha256"],
                    "dry_run_sha256": plan["dry_run_sha256"],
                },
            )
            assert wrong_auth_key.status_code == 403

            wrong_plan_hash = client.post(
                f"/v1/shrimp-animation/publish-plans/{plan['id']}/decision",
                headers={
                    "X-Shrimp-Publish-Key": "ci-step9-publish-key",
                },
                json={
                    "decision": "AUTHORIZE",
                    "reason": "wrong plan hash must fail",
                    "actor": "ci",
                    "plan_sha256": "0" * 64,
                    "dry_run_sha256": plan["dry_run_sha256"],
                },
            )
            assert wrong_plan_hash.status_code == 409

            wrong_dry_hash = client.post(
                f"/v1/shrimp-animation/publish-plans/{plan['id']}/decision",
                headers={
                    "X-Shrimp-Publish-Key": "ci-step9-publish-key",
                },
                json={
                    "decision": "AUTHORIZE",
                    "reason": "wrong dry-run hash must fail",
                    "actor": "ci",
                    "plan_sha256": plan["plan_sha256"],
                    "dry_run_sha256": "0" * 64,
                },
            )
            assert wrong_dry_hash.status_code == 409

            authorized = client.post(
                f"/v1/shrimp-animation/publish-plans/{plan['id']}/decision",
                headers={
                    "X-Shrimp-Publish-Key": "ci-step9-publish-key",
                },
                json={
                    "decision": "AUTHORIZE",
                    "reason": (
                        "Human authorizes this exact dry-run Publisher Plan "
                        "without executing any external publish."
                    ),
                    "actor": "ci-shrimp-step9-authorizer",
                    "plan_sha256": plan["plan_sha256"],
                    "dry_run_sha256": plan["dry_run_sha256"],
                },
            )
            assert authorized.status_code == 200
            authorized_payload = authorized.json()
            assert authorized_payload["plan_status"] == "PUBLISH_AUTHORIZED"
            assert authorized_payload["decision"] == "AUTHORIZE"
            assert authorized_payload["execution_enabled"] is False
            assert authorized_payload["publish_performed"] is False
            assert authorized_payload["external_publish_performed"] is False
            assert authorized_payload["network_request_count"] == 0
            assert authorized_payload["credential_access_count"] == 0
            assert authorized_payload["external_write_count"] == 0
            assert authorized_payload["next_stage"] == (
                "CONTROLLED_PUBLISHER_EXECUTION"
            )

            terminal = get_publish_plan(plan["id"])
            assert terminal["plan_status"] == "PUBLISH_AUTHORIZED"
            assert terminal["execution_enabled"] is False
            assert terminal["publish_performed"] is False
            assert len(terminal["decisions"]) == 1
            assert terminal["decisions"][0]["decision"] == "AUTHORIZE"
            assert terminal["decisions"][0]["decision_status"] == "CURRENT"

            repeated = client.post(
                f"/v1/shrimp-animation/publish-plans/{plan['id']}/decision",
                headers={
                    "X-Shrimp-Publish-Key": "ci-step9-publish-key",
                },
                json={
                    "decision": "REJECT",
                    "reason": "terminal plan cannot be decided twice",
                    "actor": "ci",
                    "plan_sha256": plan["plan_sha256"],
                    "dry_run_sha256": plan["dry_run_sha256"],
                },
            )
            assert repeated.status_code == 409

            with pytest.raises(Exception):
                with engine.begin() as db:
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_publish_plans
                          SET publish_metadata='{"title":"tampered"}'::jsonb
                          WHERE id=CAST(:id AS uuid)
                        """),
                        {"id": plan["id"]},
                    )

            changed_brief = approve_brief.model_copy(
                update={
                    "premise": (
                        "Step 9 upstream change invalidates the frozen "
                        "publishing authorization evidence."
                    )
                }
            )
            update_shrimp_content_brief(
                approve_job,
                changed_brief,
                actor="ci-shrimp-step9-invalidate",
            )
            stale_plan = get_publish_plan(plan["id"])
            assert stale_plan["plan_status"] == "STALE"
            assert stale_plan["decisions"][0]["decision_status"] == "STALE"
            stale_job = get_shrimp_animation_job(approve_job)
            assert stale_job["shrimp_animation"]["review_status"] == "STALE"

            # Remove the fixed-fingerprint fixture before creating the
            # independent REJECT candidate.
            with engine.begin() as db:
                db.execute(
                    text("""
                      DELETE FROM digital_asset_opportunities
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": str(opportunity_id)},
                )
            opportunity_ids.remove(str(opportunity_id))

            opportunity_id2, proposal_id2 = _create_proposal()
            opportunity_ids.append(str(opportunity_id2))
            reject_job, _, _ = _build_review_ready_job(
                proposal_id2,
                reusable_media,
                temp_dir=temp_dir,
                requested_by="ci-shrimp-step9-reject",
            )
            _approve_step8(reject_job)

            reject_plan_response = client.post(
                f"/v1/shrimp-animation/jobs/{reject_job}/publish-plans",
                headers={
                    "X-Shrimp-Publish-Key": "ci-step9-publish-key",
                },
                json=_plan_payload(
                    "ci-youtube",
                    "Step 9 Independent Reject CI Episode",
                ),
            )
            assert reject_plan_response.status_code == 201
            reject_plan = reject_plan_response.json()

            rejected = client.post(
                f"/v1/shrimp-animation/publish-plans/"
                f"{reject_plan['id']}/decision",
                headers={
                    "X-Shrimp-Publish-Key": "ci-step9-publish-key",
                },
                json={
                    "decision": "REJECT",
                    "reason": "Human rejects this platform publishing plan.",
                    "actor": "ci-shrimp-step9-rejector",
                    "plan_sha256": reject_plan["plan_sha256"],
                    "dry_run_sha256": reject_plan["dry_run_sha256"],
                },
            )
            assert rejected.status_code == 200
            reject_payload = rejected.json()
            assert reject_payload["plan_status"] == "PUBLISH_REJECTED"
            assert reject_payload["execution_enabled"] is False
            assert reject_payload["publish_performed"] is False
            assert reject_payload["external_publish_performed"] is False
            assert reject_payload["next_stage"] == "NONE"
    finally:
        with engine.begin() as db:
            for opportunity_id in opportunity_ids:
                db.execute(
                    text("""
                      DELETE FROM digital_asset_opportunities
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": opportunity_id},
                )
            db.execute(
                text("""
                  DELETE FROM shrimp_animation_publish_targets
                  WHERE target_key LIKE 'ci-%'
                """)
            )
        _cleanup_registry()
