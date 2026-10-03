from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.publisher_execution import (
    get_publish_execution,
)
from app.providers.animation.shrimp.publisher_execution_adapter import (
    BilibiliControlledPublisherAdapter,
    PublishReceipt,
    PublisherWriteOutcomeUnknown,
    UploadReceipt,
    YouTubeControlledPublisherAdapter,
    sha256_json,
)
from app.providers.animation.shrimp.provider import update_shrimp_content_brief

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


class AmbiguousControlledBilibiliAdapter:
    kind = "BILIBILI_CONTROLLED"
    platform = "BILIBILI"

    def __init__(self) -> None:
        self.upload_calls = 0
        self.upload_reconcile_calls = 0
        self.publish_calls = 0
        self.publish_reconcile_calls = 0
        self.upload_receipts: dict[str, UploadReceipt] = {}
        self.publish_receipts: dict[str, PublishReceipt] = {}

    def validate_target(self, execution):
        assert execution["platform"] == "BILIBILI"

    def upload(self, execution, *, media_path, idempotency_key):
        self.upload_calls += 1
        receipt = UploadReceipt(
            provider_upload_id="fixture-upload-" + idempotency_key[:16],
            state="UPLOADED",
            provider_write_performed=True,
            metadata={
                "fixture": True,
                "sacrificial_account": execution["account_reference"],
            },
        )
        self.upload_receipts[idempotency_key] = receipt
        raise PublisherWriteOutcomeUnknown(
            "fixture upload response lost after provider accepted write",
            phase="UPLOAD",
            error_type="FixtureTimeout",
            evidence_sha256=sha256_json({
                "phase": "UPLOAD",
                "idempotency_key": idempotency_key,
            }),
        )

    def reconcile_upload(
        self,
        execution,
        *,
        idempotency_key,
        provider_upload_id,
    ):
        self.upload_reconcile_calls += 1
        return self.upload_receipts.get(idempotency_key)

    def publish(
        self,
        execution,
        *,
        provider_upload_id,
        idempotency_key,
    ):
        self.publish_calls += 1
        receipt = PublishReceipt(
            provider_publish_id="fixture-publish-" + idempotency_key[:16],
            url="https://sacrificial.example.invalid/video/"
                + idempotency_key[:12],
            state="PUBLISHED",
            provider_write_performed=True,
            metadata={
                "fixture": True,
                "provider_upload_id": provider_upload_id,
            },
        )
        self.publish_receipts[idempotency_key] = receipt
        raise PublisherWriteOutcomeUnknown(
            "fixture publish response lost after provider accepted write",
            phase="PUBLISH",
            error_type="FixtureConnectionReset",
            evidence_sha256=sha256_json({
                "phase": "PUBLISH",
                "idempotency_key": idempotency_key,
            }),
        )

    def reconcile_publish(
        self,
        execution,
        *,
        idempotency_key,
        provider_publish_id,
    ):
        self.publish_reconcile_calls += 1
        return self.publish_receipts.get(idempotency_key)


def _register_target(
    client: TestClient,
    *,
    key: str,
    account: str,
) -> dict:
    response = client.post(
        "/v1/shrimp-animation/publish-targets",
        headers={"X-Shrimp-Publish-Key": "ci-step10-auth-key"},
        json={
            "target_key": key,
            "platform": "BILIBILI",
            "display_name": key,
            "account_reference": account,
            "metadata_constraints": {},
            "actor": "ci-step10",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _authorize_plan(
    client: TestClient,
    *,
    job_id,
    target_key: str,
    title: str,
) -> dict:
    created = client.post(
        f"/v1/shrimp-animation/jobs/{job_id}/publish-plans",
        headers={"X-Shrimp-Publish-Key": "ci-step10-auth-key"},
        json={
            "target_key": target_key,
            "publish_metadata": {
                "title": title,
                "description": "Step 10 controlled publisher CI.",
                "tags": ["step10", "controlled"],
                "category": "ci",
                "visibility": "DRAFT",
            },
            "actor": "ci-step10-plan",
        },
    )
    assert created.status_code == 201, created.text
    plan = created.json()

    authorized = client.post(
        f"/v1/shrimp-animation/publish-plans/{plan['id']}/decision",
        headers={"X-Shrimp-Publish-Key": "ci-step10-auth-key"},
        json={
            "decision": "AUTHORIZE",
            "reason": "Authorize exact Step 10 controlled publisher plan.",
            "actor": "ci-step10-authorizer",
            "plan_sha256": plan["plan_sha256"],
            "dry_run_sha256": plan["dry_run_sha256"],
        },
    )
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["plan_status"] == "PUBLISH_AUTHORIZED"
    return plan


def test_step10_controlled_publisher_exactly_once_and_reconcile(
    monkeypatch,
):
    opportunity_ids: list[str] = []
    target_keys = [
        "ci-bilibili-sacrificial",
        "ci-bilibili-denied-account",
    ]
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
            "ci-step10-execution-key",
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
            "ci-sacrificial-account",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_denied_account_refs",
            "ci-real-account",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_allowed_target_keys",
            "ci-bilibili-sacrificial,ci-bilibili-denied-account",
        )
        monkeypatch.setattr(
            settings,
            "shrimp_publish_execution_denied_target_keys",
            "prod-bilibili-main",
        )

        client = TestClient(app)
        _register_target(
            client,
            key="ci-bilibili-sacrificial",
            account="ci-sacrificial-account",
        )
        _register_target(
            client,
            key="ci-bilibili-denied-account",
            account="ci-real-account",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            opportunity_id, proposal_id = _create_proposal()
            opportunity_ids.append(str(opportunity_id))
            job_id, brief, _ = _build_review_ready_job(
                proposal_id,
                reusable_media,
                temp_dir=temp_dir,
                requested_by="ci-shrimp-step10",
            )
            workspace = _approve_step8(job_id)
            assert workspace["review_status"] == "RELEASE_APPROVED"

            plan = _authorize_plan(
                client,
                job_id=job_id,
                target_key="ci-bilibili-sacrificial",
                title="Step 10 Sacrificial Publish",
            )

            denied_plan = _authorize_plan(
                client,
                job_id=job_id,
                target_key="ci-bilibili-denied-account",
                title="Step 10 Denied Real Account",
            )

            wrong_execution_key = client.post(
                f"/v1/shrimp-animation/publish-plans/"
                f"{plan['id']}/execution",
                headers={
                    "X-Shrimp-Publish-Execution-Key": "wrong-key",
                },
                json={"actor": "ci-step10"},
            )
            assert wrong_execution_key.status_code == 403

            monkeypatch.setattr(
                settings,
                "shrimp_publish_execution_key",
                "ci-step10-auth-key",
            )
            reused_key = client.post(
                f"/v1/shrimp-animation/publish-plans/"
                f"{plan['id']}/execution",
                headers={
                    "X-Shrimp-Publish-Execution-Key": "ci-step10-auth-key",
                },
                json={"actor": "ci-step10"},
            )
            assert reused_key.status_code == 503
            monkeypatch.setattr(
                settings,
                "shrimp_publish_execution_key",
                "ci-step10-execution-key",
            )

            denied_execution = client.post(
                f"/v1/shrimp-animation/publish-plans/"
                f"{denied_plan['id']}/execution",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10-execution-key",
                },
                json={"actor": "ci-step10"},
            )
            assert denied_execution.status_code == 409
            assert "denylisted" in denied_execution.text

            created = client.post(
                f"/v1/shrimp-animation/publish-plans/"
                f"{plan['id']}/execution",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10-execution-key",
                },
                json={"actor": "ci-step10"},
            )
            assert created.status_code == 201, created.text
            execution = created.json()
            assert execution["execution_status"] == "SNAPSHOT_CREATED"
            assert execution["upload_write_count"] == 0
            assert execution["publish_write_count"] == 0
            assert execution["automatic_execution"] is False
            assert execution["external_publish_performed"] is False
            assert execution["account_reference"] == "ci-sacrificial-account"
            assert len(execution["execution_sha256"]) == 64
            assert len(execution["upload_idempotency_key"]) == 64
            assert len(execution["publish_idempotency_key"]) == 64

            replay = client.post(
                f"/v1/shrimp-animation/publish-plans/"
                f"{plan['id']}/execution",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10-execution-key",
                },
                json={"actor": "ci-step10"},
            )
            assert replay.status_code == 201
            assert replay.json()["id"] == execution["id"]
            assert replay.json()["replayed"] is True

            fixture = AmbiguousControlledBilibiliAdapter()
            import app.providers.animation.shrimp.publisher_execution as svc
            monkeypatch.setattr(
                svc,
                "get_publisher_execution_adapter",
                lambda kind: fixture,
            )

            upload_unknown = client.post(
                f"/v1/shrimp-animation/publish-executions/"
                f"{execution['id']}/upload",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10-execution-key",
                },
                json={"actor": "ci-step10-upload"},
            )
            assert upload_unknown.status_code == 409
            after_upload_unknown = get_publish_execution(execution["id"])
            assert after_upload_unknown["execution_status"] == "UPLOAD_UNKNOWN"
            assert after_upload_unknown["upload_outcome"] == "AMBIGUOUS"
            assert after_upload_unknown["upload_write_count"] == 1
            assert fixture.upload_calls == 1

            blind_upload_retry = client.post(
                f"/v1/shrimp-animation/publish-executions/"
                f"{execution['id']}/upload",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10-execution-key",
                },
                json={"actor": "ci-step10-upload-retry"},
            )
            assert blind_upload_retry.status_code == 409
            assert "reconcile" in blind_upload_retry.text.lower()
            assert fixture.upload_calls == 1

            reconciled_upload = client.post(
                f"/v1/shrimp-animation/publish-executions/"
                f"{execution['id']}/upload/reconcile",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10-execution-key",
                },
                json={"actor": "ci-step10-upload-reconcile"},
            )
            assert reconciled_upload.status_code == 200
            upload_state = reconciled_upload.json()
            assert upload_state["execution_status"] == "UPLOADED"
            assert upload_state["upload_outcome"] == "RECONCILED_ACCEPTED"
            assert upload_state["upload_write_count"] == 1
            assert fixture.upload_reconcile_calls == 1

            publish_unknown = client.post(
                f"/v1/shrimp-animation/publish-executions/"
                f"{execution['id']}/publish",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10-execution-key",
                },
                json={"actor": "ci-step10-publish"},
            )
            assert publish_unknown.status_code == 409
            after_publish_unknown = get_publish_execution(execution["id"])
            assert after_publish_unknown["execution_status"] == "PUBLISH_UNKNOWN"
            assert after_publish_unknown["publish_outcome"] == "AMBIGUOUS"
            assert after_publish_unknown["publish_write_count"] == 1
            assert fixture.publish_calls == 1

            blind_publish_retry = client.post(
                f"/v1/shrimp-animation/publish-executions/"
                f"{execution['id']}/publish",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10-execution-key",
                },
                json={"actor": "ci-step10-publish-retry"},
            )
            assert blind_publish_retry.status_code == 409
            assert "reconcile" in blind_publish_retry.text.lower()
            assert fixture.publish_calls == 1

            changed_brief = brief.model_copy(
                update={
                    "premise": (
                        "Step 10 source changes after an ambiguous provider "
                        "publish write; only reconciliation may continue."
                    )
                }
            )
            update_shrimp_content_brief(
                job_id,
                changed_brief,
                actor="ci-step10-stale-source",
            )
            stale_execution = get_publish_execution(execution["id"])
            assert stale_execution["source_stale"] is True
            assert stale_execution["execution_status"] == "PUBLISH_UNKNOWN"

            reconciled_publish = client.post(
                f"/v1/shrimp-animation/publish-executions/"
                f"{execution['id']}/publish/reconcile",
                headers={
                    "X-Shrimp-Publish-Execution-Key":
                        "ci-step10-execution-key",
                },
                json={"actor": "ci-step10-publish-reconcile"},
            )
            assert reconciled_publish.status_code == 200
            final = reconciled_publish.json()
            assert final["execution_status"] == "PUBLISHED"
            assert final["publish_outcome"] == "RECONCILED_ACCEPTED"
            assert final["upload_write_count"] == 1
            assert final["publish_write_count"] == 1
            assert final["source_stale"] is True
            assert final["external_publish_performed"] is True
            assert fixture.publish_reconcile_calls == 1
            assert fixture.publish_calls == 1

            event_types = [item["event_type"] for item in final["events"]]
            assert event_types == [
                "SNAPSHOT_CREATED",
                "UPLOAD_REQUESTED",
                "UPLOAD_AMBIGUOUS",
                "UPLOAD_RECONCILED_ACCEPTED",
                "PUBLISH_REQUESTED",
                "PUBLISH_AMBIGUOUS",
                "SOURCE_STALE",
                "PUBLISH_RECONCILED_ACCEPTED",
            ]

            with pytest.raises(Exception):
                with engine.begin() as db:
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_publish_executions
                          SET execution_sha256=:sha
                          WHERE id=CAST(:id AS uuid)
                        """),
                        {
                            "id": execution["id"],
                            "sha": "f" * 64,
                        },
                    )

            with pytest.raises(Exception):
                with engine.begin() as db:
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_publish_execution_events
                          SET actor='tampered'
                          WHERE execution_id=CAST(:id AS uuid)
                        """),
                        {"id": execution["id"]},
                    )

            assert BilibiliControlledPublisherAdapter().platform == "BILIBILI"
            assert YouTubeControlledPublisherAdapter().platform == "YOUTUBE"
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
                  WHERE target_key = ANY(:keys)
                """),
                {"keys": target_keys},
            )
        _cleanup_registry()
