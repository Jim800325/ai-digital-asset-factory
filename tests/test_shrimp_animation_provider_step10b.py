from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.bilibili_live_acceptance import (
    get_bilibili_live_acceptance,
    run_bilibili_live_acceptance,
)
from app.providers.animation.shrimp.publisher_execution_adapter import (
    PublishReceipt,
    PublisherWriteOutcomeUnknown,
    UploadReceipt,
    sha256_json,
)

from tests.test_shrimp_animation_provider_step4 import (
    _cleanup_registry,
    _create_proposal,
    _seed_registry,
)
from tests.test_shrimp_animation_provider_step8 import (
    _build_review_ready_job,
)
from tests.test_shrimp_animation_provider_step9 import (
    _approve_step8,
)
from tests.test_shrimp_animation_provider_step10 import (
    _authorize_plan,
    _register_target,
)


class BilibiliStep10BFixture:
    kind = "BILIBILI_CONTROLLED"
    platform = "BILIBILI"

    def __init__(self) -> None:
        self.aid = "112233445566"
        self.bvid = "BV1Step10BFixture"
        self.present = False
        self.published = False
        self.deleted = False
        self.upload_calls = 0
        self.publish_calls = 0
        self.cleanup_calls = 0
        self.cleanup_verify_calls = 0

    @staticmethod
    def _marker(key: str) -> str:
        return f"[shrimp-step10b:{key}]"

    def validate_target(self, execution):
        assert execution["platform"] == "BILIBILI"
        assert execution["account_reference"] == "123456789"

    def preflight(self, execution):
        self.validate_target(execution)
        return {
            "mid": "123456789",
            "uname": "CI Bilibili Sacrificial",
            "is_login": True,
            "level": 6,
        }

    def upload(self, execution, *, media_path, idempotency_key):
        self.validate_target(execution)
        assert media_path.is_file()
        self.upload_calls += 1
        self.present = True
        return UploadReceipt(
            provider_upload_id=self.aid,
            state="UPLOADED_PRIVATE",
            provider_write_performed=True,
            metadata={
                "aid": self.aid,
                "bvid": self.bvid,
                "url": f"https://www.bilibili.com/video/{self.bvid}",
                "is_only_self": 1,
                "marker": self._marker(idempotency_key),
            },
        )

    def reconcile_upload(
        self,
        execution,
        *,
        idempotency_key,
        provider_upload_id,
    ):
        if not self.present:
            return None
        return UploadReceipt(
            provider_upload_id=self.aid,
            state="UPLOADED_PRIVATE",
            provider_write_performed=False,
            metadata={
                "aid": self.aid,
                "bvid": self.bvid,
                "is_only_self": 1,
                "marker": self._marker(idempotency_key),
            },
        )

    def publish(
        self,
        execution,
        *,
        provider_upload_id,
        idempotency_key,
    ):
        assert self.present
        assert provider_upload_id == self.aid
        self.publish_calls += 1
        self.published = True
        return PublishReceipt(
            provider_publish_id=self.aid,
            url=f"https://www.bilibili.com/video/{self.bvid}",
            state="PRIVATE",
            provider_write_performed=True,
            metadata={
                "aid": self.aid,
                "bvid": self.bvid,
                "is_only_self": 1,
                "marker": self._marker(
                    execution["upload_idempotency_key"]
                ),
            },
        )

    def reconcile_publish(
        self,
        execution,
        *,
        idempotency_key,
        provider_publish_id,
    ):
        if not self.present or not self.published:
            return None
        return PublishReceipt(
            provider_publish_id=self.aid,
            url=f"https://www.bilibili.com/video/{self.bvid}",
            state="PRIVATE",
            provider_write_performed=False,
            metadata={
                "aid": self.aid,
                "bvid": self.bvid,
                "is_only_self": 1,
            },
        )

    def read_back_archive(self, execution, *, aid, marker):
        assert self.present
        assert aid == self.aid
        assert marker == self._marker(execution["upload_idempotency_key"])
        return {
            "aid": self.aid,
            "bvid": self.bvid,
            "title": execution["publish_metadata"]["title"],
            "desc": (
                execution["publish_metadata"]["description"]
                + "\n\n"
                + marker
            ),
            "state": 0,
            "state_desc": "仅自己可见",
            "is_only_self": 1,
            "owner_mid": "123456789",
        }

    def delete_archive(self, execution, *, aid):
        assert self.present
        assert aid == self.aid
        self.cleanup_calls += 1
        self.present = False
        self.deleted = True
        raise PublisherWriteOutcomeUnknown(
            "fixture delete response lost after delete accepted",
            phase="CLEANUP",
            error_type="FixtureCleanupTimeout",
            evidence_sha256=sha256_json({
                "phase": "CLEANUP",
                "aid": aid,
            }),
        )

    def verify_deleted(self, execution, *, aid):
        self.cleanup_verify_calls += 1
        assert aid == self.aid
        return not self.present


def test_step10b_bilibili_private_live_acceptance_contract(monkeypatch):
    opportunity_ids: list[str] = []
    target_key = "ci-bilibili-live-sacrificial"
    try:
        register_shrimp_animation_provider()
        reusable_media = _seed_registry()

        monkeypatch.setattr(
            settings,
            "shrimp_human_review_key",
            "ci-step8-review-key",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_authorization_key",
            "ci-step10-auth-key",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_key",
            "ci-step10b-execution-key",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_executor_enabled",
            True,
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_adapter",
            "BILIBILI_CONTROLLED",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_allowed_account_refs",
            "123456789",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_denied_account_refs",
            "987654321",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_allowed_target_keys",
            target_key,
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_denied_target_keys",
            "prod-bilibili-main",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_bilibili_live_acceptance_enabled",
            True,
        )
        monkeypatch.setattr(
            settings,
            "shrimp_bilibili_live_acceptance_key",
            "ci-step10b-live-key",
        )

        client = TestClient(app)
        _register_target(
            client,
            key=target_key,
            account="123456789",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            opportunity_id, proposal_id = _create_proposal()
            opportunity_ids.append(str(opportunity_id))

            job_id, _, _ = _build_review_ready_job(
                proposal_id,
                reusable_media,
                temp_dir=temp_dir,
                requested_by="ci-shrimp-step10b",
            )
            workspace = _approve_step8(job_id)
            assert workspace["review_status"] == "RELEASE_APPROVED"

            plan = _authorize_plan(
                client,
                job_id=job_id,
                target_key=target_key,
                title="Step 10B Bilibili Private Acceptance",
            )

            created = client.post(
                f"/v1/shrimp-animation/publish-plans/"
                f"{plan['id']}/execution",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10b-execution-key",
                },
                json={"actor": "ci-step10b-execution"},
            )
            assert created.status_code == 201, created.text
            execution = created.json()
            assert execution["execution_status"] == "SNAPSHOT_CREATED"
            assert execution["execution_adapter"] == "BILIBILI_CONTROLLED"

            fixture = BilibiliStep10BFixture()
            result = run_bilibili_live_acceptance(
                execution["id"],
                actor="ci-step10b-live",
                adapter=fixture,
            )

            assert result["acceptance_status"] == "CLEANED_UP"
            assert result["expected_mid"] == "123456789"
            assert result["actual_mid"] == "123456789"
            assert str(result["aid"]) == fixture.aid
            assert result["bvid"] == fixture.bvid
            assert result["is_only_self"] == 1
            assert result["provider_read_back_verified"] is True
            assert result["private_visibility_verified"] is True
            assert result["cleanup_write_count"] == 1
            assert result["cleanup_outcome"] == "RECONCILED_DELETED"
            assert result["cleanup_verified"] is True
            assert result["cleanup_performed"] is True
            assert result["external_upload_performed"] is True
            assert result["external_publish_performed"] is True
            assert result["production_account_touched"] is False
            assert result["public_visibility_observed"] is False

            assert fixture.upload_calls == 1
            assert fixture.publish_calls == 1
            assert fixture.cleanup_calls == 1
            assert fixture.cleanup_verify_calls == 1

            replay = run_bilibili_live_acceptance(
                execution["id"],
                actor="ci-step10b-replay",
                adapter=fixture,
            )
            assert replay["acceptance_status"] == "CLEANED_UP"
            assert replay["replayed"] is True
            assert fixture.upload_calls == 1
            assert fixture.publish_calls == 1
            assert fixture.cleanup_calls == 1

            audit = get_bilibili_live_acceptance(execution["id"])
            assert audit is not None

            with pytest.raises(Exception):
                with engine.begin() as db:
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_bilibili_live_acceptance_runs
                          SET expected_mid='999999'
                          WHERE execution_id=CAST(:execution_id AS uuid)
                        """),
                        {"execution_id": execution["id"]},
                    )

            monkeypatch.setattr(
                settings,
                "shrimp_bilibili_live_acceptance_key",
                "ci-step10b-execution-key",
            )
            reused = client.post(
                f"/v1/shrimp-animation/publish-executions/"
                f"{execution['id']}/bilibili-live-acceptance",
                headers={
                    "X-Shrimp-Bilibili-Live-Acceptance-Key":
                        "ci-step10b-execution-key",
                },
                json={"actor": "ci-step10b"},
            )
            assert reused.status_code == 503

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
                  WHERE target_key=:target_key
                """),
                {"target_key": target_key},
            )
        _cleanup_registry()


def test_step10b_readiness_redacts_bilibili_credentials(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setattr(
        settings,
        "preview_database_url",
        settings.database_url,
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_authorization_key",
        "ci-readiness-publish-auth-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_key",
        "ci-readiness-execution-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_live_acceptance_key",
        "ci-readiness-bilibili-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_executor_enabled",
        True,
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_adapter",
        "BILIBILI_CONTROLLED",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_live_acceptance_enabled",
        True,
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_sessdata",
        "ci-secret-sessdata",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_bili_jct",
        "ci-secret-jct",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_dede_user_id",
        "123456789",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_allowed_account_refs",
        "123456789",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_denied_account_refs",
        "987654321",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_allowed_target_keys",
        "ci-bilibili-readiness",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_denied_target_keys",
        "prod-bilibili-main",
    )

    response = TestClient(app).get(
        "/v1/shrimp-animation/bilibili-live-acceptance/readiness"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["secrets_redacted"] is True
    assert payload["checks"]["vercel_preview"] is True
    assert payload["checks"]["preview_database_isolated"] is True
    assert payload["checks"]["publish_authorization_gate"] is True
    assert payload["checks"]["publish_execution_gate"] is True
    assert payload["checks"]["publish_executor_enabled"] is True
    assert payload["checks"]["bilibili_controlled_adapter"] is True
    assert payload["checks"]["bilibili_live_acceptance_gate"] is True
    assert payload["checks"]["bilibili_live_acceptance_enabled"] is True
    assert payload["checks"]["bilibili_cookie_credentials_present"] is True
    assert payload["checks"]["sacrificial_account_allowlist_present"] is True
    assert payload["checks"]["real_account_denylist_present"] is True
    assert payload["checks"]["sacrificial_target_allowlist_present"] is True
    assert payload["checks"]["real_target_denylist_present"] is True
    assert payload["checks"]["allowlist_denylist_disjoint"] is True
    assert payload["checks"]["runnable_bilibili_execution_present"] is False
    assert payload["status"] == "BLOCKED"
    assert "runnable_bilibili_execution_present" in payload["blockers"]

    serialized = str(payload)
    assert "ci-secret-sessdata" not in serialized
    assert "ci-secret-jct" not in serialized
    assert "ci-readiness-bilibili-key" not in serialized
