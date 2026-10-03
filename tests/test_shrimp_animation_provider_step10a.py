from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.publisher_execution_adapter import (
    PublishReceipt,
    PublisherWriteOutcomeUnknown,
    UploadReceipt,
    sha256_json,
)
from app.providers.animation.shrimp.youtube_live_acceptance import (
    get_youtube_live_acceptance,
    run_youtube_live_acceptance,
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


class YouTubeStep10AFixture:
    kind = "YOUTUBE_CONTROLLED"
    platform = "YOUTUBE"

    def __init__(self) -> None:
        self.video_id = "ci-youtube-video-step10a"
        self.present = False
        self.published = False
        self.deleted = False
        self.upload_calls = 0
        self.publish_calls = 0
        self.cleanup_calls = 0
        self.cleanup_verify_calls = 0

    @staticmethod
    def _marker(key: str) -> str:
        return f"[shrimp-step10a:{key}]"

    def validate_target(self, execution):
        assert execution["platform"] == "YOUTUBE"
        assert execution["account_reference"] == "UCciSacrificialChannel"

    def preflight(self, execution):
        self.validate_target(execution)
        return {
            "channel_id": "UCciSacrificialChannel",
            "channel_title": "CI Sacrificial Channel",
            "uploads_playlist_id": "UUciSacrificialChannel",
            "category_id": "22",
        }

    def upload(self, execution, *, media_path, idempotency_key):
        self.validate_target(execution)
        assert media_path.is_file()
        self.upload_calls += 1
        self.present = True
        return UploadReceipt(
            provider_upload_id=self.video_id,
            state="UPLOADED",
            provider_write_performed=True,
            metadata={
                "privacy_status": "private",
                "reconciliation_marker": self._marker(idempotency_key),
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
            provider_upload_id=self.video_id,
            state="UPLOADED",
            provider_write_performed=False,
            metadata={
                "privacy_status": "private",
                "reconciliation_marker": self._marker(idempotency_key),
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
        assert provider_upload_id == self.video_id
        self.publish_calls += 1
        self.published = True
        return PublishReceipt(
            provider_publish_id=self.video_id,
            url=f"https://www.youtube.com/watch?v={self.video_id}",
            state="PRIVATE",
            provider_write_performed=True,
            metadata={
                "privacy_status": "private",
                "final_title": execution["publish_metadata"]["title"],
            },
        )

    def reconcile_publish(
        self,
        execution,
        *,
        idempotency_key,
        provider_publish_id,
    ):
        if not self.published or not self.present:
            return None
        return PublishReceipt(
            provider_publish_id=self.video_id,
            url=f"https://www.youtube.com/watch?v={self.video_id}",
            state="PRIVATE",
            provider_write_performed=False,
            metadata={"privacy_status": "private"},
        )

    def read_back_video(self, execution, *, video_id, marker=None):
        assert self.present
        assert video_id == self.video_id
        if marker is not None:
            assert marker == self._marker(execution["upload_idempotency_key"])
        return {
            "video_id": self.video_id,
            "channel_id": "UCciSacrificialChannel",
            "title": execution["publish_metadata"]["title"],
            "description": (
                execution["publish_metadata"]["description"]
                + "\n\n"
                + self._marker(execution["upload_idempotency_key"])
            ),
            "category_id": "22",
            "privacy_status": "private",
            "processing_status": "succeeded",
            "upload_status": "uploaded",
        }

    def delete_video(self, execution, *, video_id):
        assert self.present
        assert video_id == self.video_id
        self.cleanup_calls += 1
        self.present = False
        self.deleted = True
        raise PublisherWriteOutcomeUnknown(
            "fixture cleanup response lost after delete accepted",
            phase="CLEANUP",
            error_type="FixtureCleanupTimeout",
            evidence_sha256=sha256_json(
                {
                    "phase": "CLEANUP",
                    "video_id": video_id,
                }
            ),
        )

    def verify_deleted(self, execution, *, video_id):
        self.cleanup_verify_calls += 1
        assert video_id == self.video_id
        return not self.present


def _register_youtube_target(client: TestClient) -> dict:
    response = client.post(
        "/v1/shrimp-animation/publish-targets",
        headers={"X-Shrimp-Publish-Key": "ci-step10a-auth-key"},
        json={
            "target_key": "ci-youtube-sacrificial",
            "platform": "YOUTUBE",
            "display_name": "CI YouTube Sacrificial",
            "account_reference": "UCciSacrificialChannel",
            "metadata_constraints": {
                "privacy": "private",
                "live_acceptance": True,
            },
            "actor": "ci-step10a",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _authorize_youtube_plan(client: TestClient, *, job_id) -> dict:
    created = client.post(
        f"/v1/shrimp-animation/jobs/{job_id}/publish-plans",
        headers={"X-Shrimp-Publish-Key": "ci-step10a-auth-key"},
        json={
            "target_key": "ci-youtube-sacrificial",
            "publish_metadata": {
                "title": "Step 10A Sacrificial Private Video",
                "description": "Private YouTube live acceptance fixture.",
                "tags": ["step10a", "sacrificial", "private"],
                "category": "ci",
                "visibility": "DRAFT",
                "youtube_category_id": "22",
            },
            "actor": "ci-step10a-plan",
        },
    )
    assert created.status_code == 201, created.text
    plan = created.json()

    authorized = client.post(
        f"/v1/shrimp-animation/publish-plans/{plan['id']}/decision",
        headers={"X-Shrimp-Publish-Key": "ci-step10a-auth-key"},
        json={
            "decision": "AUTHORIZE",
            "reason": "Authorize exact private sacrificial YouTube acceptance.",
            "actor": "ci-step10a-authorizer",
            "plan_sha256": plan["plan_sha256"],
            "dry_run_sha256": plan["dry_run_sha256"],
        },
    )
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["plan_status"] == "PUBLISH_AUTHORIZED"
    return plan


def test_step10a_youtube_private_live_acceptance_contract(monkeypatch):
    opportunity_ids: list[str] = []
    target_key = "ci-youtube-sacrificial"
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
            "ci-step10a-auth-key",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_key",
            "ci-step10a-execution-key",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_executor_enabled",
            True,
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_adapter",
            "YOUTUBE_CONTROLLED",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_allowed_account_refs",
            "UCciSacrificialChannel",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_denied_account_refs",
            "UCprodMainChannel",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_allowed_target_keys",
            target_key,
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_denied_target_keys",
            "prod-youtube-main",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_youtube_live_acceptance_enabled",
            True,
        )
        monkeypatch.setattr(
            settings,
            "shrimp_youtube_live_acceptance_key",
            "ci-step10a-live-key",
        )

        client = TestClient(app)
        _register_youtube_target(client)

        with tempfile.TemporaryDirectory() as temp_dir:
            opportunity_id, proposal_id = _create_proposal()
            opportunity_ids.append(str(opportunity_id))
            job_id, _, _ = _build_review_ready_job(
                proposal_id,
                reusable_media,
                temp_dir=temp_dir,
                requested_by="ci-shrimp-step10a",
            )
            workspace = _approve_step8(job_id)
            assert workspace["review_status"] == "RELEASE_APPROVED"

            plan = _authorize_youtube_plan(client, job_id=job_id)

            created = client.post(
                f"/v1/shrimp-animation/publish-plans/"
                f"{plan['id']}/execution",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10a-execution-key",
                },
                json={"actor": "ci-step10a-execution"},
            )
            assert created.status_code == 201, created.text
            execution = created.json()
            assert execution["execution_status"] == "SNAPSHOT_CREATED"
            assert execution["execution_adapter"] == "YOUTUBE_CONTROLLED"

            fixture = YouTubeStep10AFixture()
            result = run_youtube_live_acceptance(
                execution["id"],
                actor="ci-step10a-live",
                adapter=fixture,
            )

            assert result["acceptance_status"] == "CLEANED_UP"
            assert result["expected_channel_id"] == "UCciSacrificialChannel"
            assert result["actual_channel_id"] == "UCciSacrificialChannel"
            assert result["video_id"] == fixture.video_id
            assert result["privacy_status"] == "private"
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

            execution_after = client.get(
                f"/v1/shrimp-animation/publish-executions/"
                f"{execution['id']}"
            ).json()
            assert execution_after["execution_status"] == "PUBLISHED"
            assert execution_after["upload_write_count"] == 1
            assert execution_after["publish_write_count"] == 1
            assert execution_after["external_publish_performed"] is True

            replay = run_youtube_live_acceptance(
                execution["id"],
                actor="ci-step10a-replay",
                adapter=fixture,
            )
            assert replay["acceptance_status"] == "CLEANED_UP"
            assert replay["replayed"] is True
            assert fixture.upload_calls == 1
            assert fixture.publish_calls == 1
            assert fixture.cleanup_calls == 1

            audit = get_youtube_live_acceptance(execution["id"])
            assert audit is not None
            assert audit["acceptance_status"] == "CLEANED_UP"

            with pytest.raises(Exception):
                with engine.begin() as db:
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_youtube_live_acceptance_runs
                          SET expected_channel_id='UCtampered'
                          WHERE execution_id=CAST(:execution_id AS uuid)
                        """),
                        {"execution_id": execution["id"]},
                    )

            # Acceptance key must be independent from Step 10 execution key.
            monkeypatch.setattr(
                settings,
                "shrimp_youtube_live_acceptance_key",
                "ci-step10a-execution-key",
            )
            reused = client.post(
                f"/v1/shrimp-animation/publish-executions/"
                f"{execution['id']}/youtube-live-acceptance",
                headers={
                    "X-Shrimp-YouTube-Live-Acceptance-Key":
                        "ci-step10a-execution-key",
                },
                json={"actor": "ci-step10a"},
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
