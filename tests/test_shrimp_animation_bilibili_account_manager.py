from __future__ import annotations

import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.human_review import get_shrimp_review_workspace
from app.providers.animation.shrimp.publishing_authorization import get_publish_plan

from tests.test_shrimp_animation_provider_step4 import (
    _cleanup_registry,
    _create_proposal,
    _seed_registry,
)
from tests.test_shrimp_animation_provider_step8 import _build_review_ready_job
from tests.test_shrimp_animation_provider_step9 import _approve_step8


def _account_payload(**updates):
    payload={
        "account_key":"ci-bili-profile",
        "display_name":"CI Bilibili Profile",
        "mid":"991234567",
        "tags":["ci","shrimp"],
        "default_tid":122,
        "default_copyright":"ORIGINAL",
        "default_description":"Default account description",
        "default_tags":["default-a","default-b"],
        "cover_strategy":"OPTIONAL",
        "daily_publish_limit":3,
        "publish_window_start":None,
        "publish_window_end":None,
        "timezone":"Asia/Shanghai",
        "safety_policy":{
            "mode":"SACRIFICIAL",
            "require_global_allowlist":True,
            "allow_public_visibility":False,
        },
        "actor":"ci-account-manager",
    }
    payload.update(updates)
    return payload


def test_bilibili_account_registry_create_update_and_redaction(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_publish_authorization_key",
        "ci-account-manager-key",
    )
    client=TestClient(app)
    try:
        wrong=client.post(
            "/v1/shrimp-animation/bilibili-accounts",
            headers={"X-Shrimp-Publish-Key":"wrong"},
            json=_account_payload(),
        )
        assert wrong.status_code == 403

        created=client.post(
            "/v1/shrimp-animation/bilibili-accounts",
            headers={"X-Shrimp-Publish-Key":"ci-account-manager-key"},
            json=_account_payload(),
        )
        assert created.status_code == 201, created.text
        account=created.json()
        assert account["account_key"] == "ci-bili-profile"
        assert account["mid"] == "991234567"
        assert account["account_status"] == "ACTIVE"
        assert account["daily_publish_limit"] == 3
        assert account["safety_policy"]["mode"] == "SACRIFICIAL"

        listing=client.get("/v1/shrimp-animation/bilibili-accounts")
        assert listing.status_code == 200
        assert any(x["account_key"]=="ci-bili-profile" for x in listing.json())

        updated=client.patch(
            "/v1/shrimp-animation/bilibili-accounts/ci-bili-profile",
            headers={"X-Shrimp-Publish-Key":"ci-account-manager-key"},
            json={
                "account_status":"INACTIVE",
                "daily_publish_limit":2,
                "default_tid":130,
                "actor":"ci-account-manager-update",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["account_status"] == "INACTIVE"
        assert updated.json()["daily_publish_limit"] == 2
        assert updated.json()["default_tid"] == 130

        serialized=str(listing.json())+str(updated.json())
        assert "ci-account-manager-key" not in serialized
        assert "SESSDATA" not in serialized
        assert "bili_jct" not in serialized
    finally:
        with engine.begin() as db:
            db.execute(text(
                "DELETE FROM shrimp_bilibili_accounts "
                "WHERE account_key='ci-bili-profile'"
            ))


def test_registered_account_defaults_and_policy_bind_into_publish_plan(
    monkeypatch,
):
    opportunity_id=None
    monkeypatch.setattr(
        settings,
        "shrimp_human_review_key",
        "ci-account-review-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_authorization_key",
        "ci-account-manager-key",
    )
    client=TestClient(app)
    try:
        register_shrimp_animation_provider()
        reusable_media=_seed_registry()

        account=client.post(
            "/v1/shrimp-animation/bilibili-accounts",
            headers={"X-Shrimp-Publish-Key":"ci-account-manager-key"},
            json=_account_payload(),
        )
        assert account.status_code == 201, account.text

        target=client.post(
            "/v1/shrimp-animation/publish-targets",
            headers={"X-Shrimp-Publish-Key":"ci-account-manager-key"},
            json={
                "target_key":"ci-bili-profile-target",
                "platform":"BILIBILI",
                "display_name":"CI Managed Bilibili",
                "account_reference":"ci-bili-profile",
                "metadata_constraints":{},
                "actor":"ci-account-target",
            },
        )
        assert target.status_code == 201, target.text

        with tempfile.TemporaryDirectory() as temp_dir:
            opportunity_id,proposal_id=_create_proposal()
            job_id,_,_=_build_review_ready_job(
                proposal_id,
                reusable_media,
                temp_dir=temp_dir,
                requested_by="ci-account-manager",
            )
            workspace=_approve_step8(job_id)
            assert workspace["review_status"] == "RELEASE_APPROVED"

            plan_response=client.post(
                f"/v1/shrimp-animation/jobs/{job_id}/publish-plans",
                headers={"X-Shrimp-Publish-Key":"ci-account-manager-key"},
                json={
                    "target_key":"ci-bili-profile-target",
                    "publish_metadata":{
                        "title":"Managed account defaults",
                        "description":"",
                        "tags":[],
                        "category":None,
                        "visibility":"DRAFT",
                    },
                    "actor":"ci-account-plan",
                },
            )
            assert plan_response.status_code == 201, plan_response.text
            plan=plan_response.json()
            stored=get_publish_plan(plan["id"])
            assert stored["account_profile_id"] is not None
            assert len(stored["account_profile_sha256"]) == 64
            assert stored["publish_metadata"]["description"] == (
                "Default account description"
            )
            assert stored["publish_metadata"]["tags"] == [
                "default-a",
                "default-b",
            ]
            assert stored["publish_metadata"]["category"] == "122"
            assert stored["plan_payload"]["account_profile_snapshot"][
                "account_key"
            ] == "ci-bili-profile"

            inactive=client.patch(
                "/v1/shrimp-animation/bilibili-accounts/ci-bili-profile",
                headers={"X-Shrimp-Publish-Key":"ci-account-manager-key"},
                json={
                    "account_status":"INACTIVE",
                    "actor":"ci-account-disable",
                },
            )
            assert inactive.status_code == 200
            stale=get_publish_plan(plan["id"])
            assert stale["plan_status"] == "STALE"

            blocked=client.post(
                f"/v1/shrimp-animation/jobs/{job_id}/publish-plans",
                headers={"X-Shrimp-Publish-Key":"ci-account-manager-key"},
                json={
                    "target_key":"ci-bili-profile-target",
                    "publish_metadata":{
                        "title":"Must be blocked",
                        "description":"",
                        "tags":[],
                        "category":None,
                        "visibility":"DRAFT",
                    },
                    "actor":"ci-account-plan-blocked",
                },
            )
            assert blocked.status_code == 409
            assert "INACTIVE" in blocked.text
    finally:
        if opportunity_id is not None:
            with engine.begin() as db:
                db.execute(
                    text("""
                      DELETE FROM digital_asset_opportunities
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id":str(opportunity_id)},
                )
        with engine.begin() as db:
            db.execute(text(
                "DELETE FROM shrimp_animation_publish_targets "
                "WHERE target_key='ci-bili-profile-target'"
            ))
            db.execute(text(
                "DELETE FROM shrimp_bilibili_accounts "
                "WHERE account_key='ci-bili-profile'"
            ))
        _cleanup_registry()
