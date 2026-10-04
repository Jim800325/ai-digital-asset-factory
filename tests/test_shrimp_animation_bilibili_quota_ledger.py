from __future__ import annotations

import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.bilibili_credentials import (
    run_credential_health_check,
)
from app.providers.animation.shrimp.bilibili_quota import settle_cleanup
from tests.test_shrimp_animation_provider_step4 import (
    _create_proposal,
    _seed_registry,
)
from tests.test_shrimp_animation_provider_step8 import _build_review_ready_job
from tests.test_shrimp_animation_provider_step9 import _approve_step8


class HealthyProbe:
    def __init__(self, credentials):
        self.credentials=credentials

    def probe_account(self, *, expected_mid: str):
        return {
            "mid":expected_mid,
            "uname":"CI Ledger",
            "is_login":True,
            "level":6,
            "publish_probe_ok":True,
        }


def _prepare_account(client, monkeypatch):
    account_key="ci-ledger-account"
    mid="82000001"
    prefix="SHRIMP_BILIBILI_LEDGER_CI"
    monkeypatch.setenv(prefix+"_SESSDATA","ci-ledger-sess")
    monkeypatch.setenv(prefix+"_BILI_JCT","ci-ledger-jct")
    monkeypatch.setenv(prefix+"_DEDE_USER_ID",mid)

    assert client.post(
        "/v1/shrimp-animation/bilibili-accounts",
        headers={"X-Shrimp-Publish-Key":"ci-ledger-publish-key"},
        json={
            "account_key":account_key,
            "display_name":"CI Ledger Account",
            "mid":mid,
            "tags":["ci"],
            "default_tid":122,
            "default_copyright":"ORIGINAL",
            "default_description":"",
            "default_tags":[],
            "cover_strategy":"OPTIONAL",
            "daily_publish_limit":3,
            "timezone":"Asia/Shanghai",
            "safety_policy":{
                "mode":"SACRIFICIAL",
                "require_global_allowlist":True,
                "allow_public_visibility":False,
            },
            "actor":"ci-ledger",
        },
    ).status_code==201

    assert client.post(
        "/v1/shrimp-animation/bilibili-credential-slots",
        headers={"X-Shrimp-Publish-Key":"ci-ledger-publish-key"},
        json={
            "account_key":account_key,
            "slot_key":"ci-ledger-slot",
            "env_prefix":prefix,
            "actor":"ci-ledger",
        },
    ).status_code==201

    health=run_credential_health_check(
        "ci-ledger-slot",
        actor="ci-ledger-health",
        adapter_factory=lambda creds:HealthyProbe(creds),
    )
    assert health["health_status"]=="HEALTHY"

    assert client.post(
        "/v1/shrimp-animation/publish-targets",
        headers={"X-Shrimp-Publish-Key":"ci-ledger-publish-key"},
        json={
            "target_key":"ci-ledger-target",
            "platform":"BILIBILI",
            "display_name":"CI Ledger Target",
            "account_reference":account_key,
            "metadata_constraints":{},
            "actor":"ci-ledger",
        },
    ).status_code==201


def _routed_plan(client, job_id):
    reservation=client.post(
        f"/v1/shrimp-animation/jobs/{job_id}/bilibili-reservations",
        headers={"X-Shrimp-Publish-Key":"ci-ledger-publish-key"},
        json={"actor":"ci-ledger-reserve"},
    )
    assert reservation.status_code==201,reservation.text
    reservation=reservation.json()
    plan=client.post(
        f"/v1/shrimp-animation/jobs/{job_id}/routed-publish-plans",
        headers={"X-Shrimp-Publish-Key":"ci-ledger-publish-key"},
        json={
            "reservation_id":reservation["id"],
            "publish_metadata":{
                "title":"CI Ledger Plan",
                "description":"",
                "tags":[],
                "category":None,
                "visibility":"DRAFT",
            },
            "actor":"ci-ledger-plan",
        },
    )
    assert plan.status_code==201,plan.text
    return reservation,plan.json()


def test_reservation_claim_publish_and_quota_ledger(monkeypatch):
    monkeypatch.setattr(settings,"shrimp_human_review_key","ci-ledger-review-key")
    monkeypatch.setattr(settings,"shrimp_publish_authorization_key","ci-ledger-publish-key")
    monkeypatch.setattr(settings,"shrimp_publish_execution_key","ci-ledger-execution-key")
    monkeypatch.setattr(settings,"shrimp_publish_execution_adapter","MOCK")
    monkeypatch.setattr(settings,"shrimp_publish_execution_allowed_account_refs","ci-ledger-account,82000001")
    monkeypatch.setattr(settings,"shrimp_publish_execution_allowed_target_keys","ci-ledger-target")

    register_shrimp_animation_provider()
    reusable=_seed_registry()
    client=TestClient(app)
    _prepare_account(client,monkeypatch)

    with tempfile.TemporaryDirectory() as temp_dir:
        _,proposal1=_create_proposal()
        reject_job,_,_=_build_review_ready_job(
            proposal1,reusable,temp_dir=temp_dir,requested_by="ci-ledger-reject"
        )
        _approve_step8(reject_job)
        reservation1,plan1=_routed_plan(client,reject_job)

        rejected=client.post(
            f"/v1/shrimp-animation/publish-plans/{plan1['id']}/decision",
            headers={"X-Shrimp-Publish-Key":"ci-ledger-publish-key"},
            json={
                "decision":"REJECT",
                "reason":"CI verifies reservation release.",
                "actor":"ci-ledger-rejector",
                "plan_sha256":plan1["plan_sha256"],
                "dry_run_sha256":plan1["dry_run_sha256"],
            },
        )
        assert rejected.status_code==200,rejected.text
        released=client.get(
            f"/v1/shrimp-animation/bilibili-reservations/{reservation1['id']}"
        ).json()
        assert released["reservation_status"]=="RELEASED"

        ledger=client.get(
            "/v1/shrimp-animation/bilibili-quota-ledger",
            params={"account_key":"ci-ledger-account"},
        ).json()
        assert "PLAN_RELEASED" in {x["entry_type"] for x in ledger}

        _,proposal2=_create_proposal()
        publish_job,_,_=_build_review_ready_job(
            proposal2,reusable,temp_dir=temp_dir,requested_by="ci-ledger-publish"
        )
        _approve_step8(publish_job)
        reservation2,plan2=_routed_plan(client,publish_job)

        authorized=client.post(
            f"/v1/shrimp-animation/publish-plans/{plan2['id']}/decision",
            headers={"X-Shrimp-Publish-Key":"ci-ledger-publish-key"},
            json={
                "decision":"AUTHORIZE",
                "reason":"CI authorizes exact routed plan.",
                "actor":"ci-ledger-authorizer",
                "plan_sha256":plan2["plan_sha256"],
                "dry_run_sha256":plan2["dry_run_sha256"],
            },
        )
        assert authorized.status_code==200,authorized.text

        execution=client.post(
            f"/v1/shrimp-animation/publish-plans/{plan2['id']}/execution",
            headers={"X-Shrimp-Publish-Execution-Key":"ci-ledger-execution-key"},
            json={"actor":"ci-ledger-execution"},
        )
        assert execution.status_code==201,execution.text
        execution=execution.json()

        claimed=client.get(
            f"/v1/shrimp-animation/bilibili-reservations/{reservation2['id']}"
        ).json()
        assert claimed["reservation_status"]=="CLAIMED"

        assert client.post(
            f"/v1/shrimp-animation/publish-executions/{execution['id']}/upload",
            headers={"X-Shrimp-Publish-Execution-Key":"ci-ledger-execution-key"},
            json={"actor":"ci-ledger-upload"},
        ).status_code==200
        published=client.post(
            f"/v1/shrimp-animation/publish-executions/{execution['id']}/publish",
            headers={"X-Shrimp-Publish-Execution-Key":"ci-ledger-execution-key"},
            json={"actor":"ci-ledger-publish"},
        )
        assert published.status_code==200,published.text

        usage=client.get(
            "/v1/shrimp-animation/bilibili-quota/ci-ledger-account"
        ).json()
        assert usage["published_units"]==1
        assert usage["claimed_units"]==0
        assert usage["available_units"]==2

        settle_cleanup(
            execution_id=execution["id"],
            source_sha256="a"*64,
            actor="ci-ledger-cleanup",
        )
        settled=client.get(
            f"/v1/shrimp-animation/bilibili-reservations/{reservation2['id']}"
        ).json()
        assert settled["reservation_status"]=="SETTLED"

        ledger=client.get(
            "/v1/shrimp-animation/bilibili-quota-ledger",
            params={"account_key":"ci-ledger-account"},
        ).json()
        types={x["entry_type"] for x in ledger}
        assert {"CLAIM_CREATED","PUBLISH_COMMITTED","CLEANUP_SETTLED"} <= types
