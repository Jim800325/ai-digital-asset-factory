import hashlib
import json

import pytest
from sqlalchemy import text

from app import preview_acceptance_bridge as bridge
import app.deployment_authorization as deployment_auth
import app.production_release as production_release
import app.release_gate as release_gate
import app.vercel_prepare_acceptance as prepare_acceptance
from app.config import settings
from app.db import engine
from app.production_execution_adapter import (
    CandidateDeployment,
    PreparedDeploymentRequest,
    REAL_PRODUCTION_PROJECT_ID,
)


def _synthetic_payloads():
    return [
        {
            "relative_path": "README.md",
            "content_bytes": (
                b"# Preview Acceptance Artifact\n\n"
                b"Controlled CI bridge fixture only.\n"
            ),
        },
        {
            "relative_path": "main.py",
            "content_bytes": (
                b"def quote_price(monthly_usd: float) -> float:\n"
                b"    if monthly_usd < 0:\n"
                b"        raise ValueError('monthly_usd must be non-negative')\n"
                b"    return round(monthly_usd * 12, 2)\n"
            ),
        },
    ]


def _tree(payloads):
    rows = []
    for item in sorted(payloads, key=lambda value: value["relative_path"]):
        data = item["content_bytes"]
        rows.append({
            "relative_path": item["relative_path"],
            "sha256": hashlib.sha256(data).hexdigest(),
            "byte_size": len(data),
        })
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _result(payloads):
    return {
        "acceptance_status": "PASSED",
        "audit_id": "ci-preview-bridge-0001",
        "gateway_mode": "PROXY",
        "live_model_verified": True,
        "budget_status": "WITHIN_BUDGET",
        "tests_passed": True,
        "external_side_effects": "DENY",
        "deployment_enabled": False,
        "release_approved": False,
        "source_tree_sha256": _tree(payloads),
        "model": "gpt-5.6-luna",
        "gateway_request_count": 2,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "estimated_cost_usd": 0.001,
        "test_log_tail": "Ran 2 tests in 0.001s\nOK\n",
    }


def test_preview_bridge_refuses_non_preview_runtime(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)

    with pytest.raises(
        bridge.PreviewAcceptanceBridgeError,
        match="only available in Vercel Preview",
    ):
        bridge.persist_preview_live_acceptance_fixture(
            _result(_synthetic_payloads()),
            _synthetic_payloads(),
        )


def test_preview_bridge_persists_two_strict_review_fixtures(monkeypatch):
    payloads = _synthetic_payloads()
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setattr(
        bridge.settings,
        "preview_database_url",
        bridge.settings.database_url,
    )
    monkeypatch.setattr(
        bridge.settings,
        "deployment_authorization_preview_only",
        True,
    )

    persisted = bridge.persist_preview_live_acceptance_fixture(
        _result(payloads),
        payloads,
    )

    assert persisted["bridge_status"] == "READY_FOR_REGISTRY_BINDING"
    assert persisted["database_source"] == "PREVIEW_DATABASE_URL"
    assert persisted["artifact_contents_persisted"] is True
    assert persisted["source_tree_sha256"] == _tree(payloads)
    assert persisted["deployment_enabled"] is False
    assert persisted["execution_enabled"] is False
    assert persisted["production_deployment_executed"] is False
    assert {item["role"] for item in persisted["fixtures"]} == {
        "authorize",
        "reject",
    }

    candidate_ids = []
    for item in persisted["fixtures"]:
        candidate_ids.append(item["release_candidate_id"])
        assert item["release_status"] == "READY_FOR_REVIEW"
        assert item["review_snapshot_complete"] is True
        assert item["deployment_enabled"] is False
        assert item["execution_enabled"] is False
        assert item["acceptance_provenance_tree_sha256"] == _tree(payloads)

    with engine.connect() as db:
        rows = db.execute(text("""
          SELECT rc.id,rc.release_status,rc.deployment_enabled,
                 rrp.content_snapshot_complete,
                 COUNT(sac.artifact_id) AS content_count
          FROM release_candidates rc
          JOIN release_review_packages rrp
            ON rrp.release_candidate_id=rc.id
          JOIN sandbox_artifacts sa ON sa.run_id=rc.run_id
          JOIN sandbox_artifact_contents sac ON sac.artifact_id=sa.id
          WHERE rc.id IN (
            CAST(:a AS uuid),
            CAST(:b AS uuid)
          )
          GROUP BY rc.id,rc.release_status,rc.deployment_enabled,
                   rrp.content_snapshot_complete
          ORDER BY rc.id
        """), {
            "a": candidate_ids[0],
            "b": candidate_ids[1],
        }).mappings().all()

    assert len(rows) == 2
    for row in rows:
        assert row["release_status"] == "READY_FOR_REVIEW"
        assert row["deployment_enabled"] is False
        assert row["content_snapshot_complete"] is True
        assert int(row["content_count"]) == len(payloads)

    # Idempotent retry returns the same controlled candidates.
    again = bridge.persist_preview_live_acceptance_fixture(
        _result(payloads),
        payloads,
    )
    assert [
        item["release_candidate_id"] for item in again["fixtures"]
    ] == candidate_ids



def test_step4a_live_prepare_acceptance_reuses_verified_bytes_and_stops_before_promotion(
    monkeypatch,
):
    payloads = _synthetic_payloads()
    tree = _tree(payloads)
    audit_id = "ci-preview-bridge-0001"

    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "a" * 40)
    monkeypatch.setenv(
        "VERCEL_URL",
        "ai-digital-asset-factory-step4a-preview.vercel.app",
    )
    monkeypatch.setattr(
        bridge.settings,
        "preview_database_url",
        bridge.settings.database_url,
    )
    monkeypatch.setattr(
        bridge.settings,
        "deployment_authorization_preview_only",
        True,
    )
    bridge.persist_preview_live_acceptance_fixture(
        _result(payloads),
        payloads,
    )

    monkeypatch.setattr(settings, "preview_database_url", settings.database_url)
    monkeypatch.setattr(settings, "deployment_authorization_preview_only", True)
    monkeypatch.setattr(
        settings,
        "production_execution_adapter",
        "VERCEL_CONTROLLED_EXECUTOR",
    )
    monkeypatch.setattr(settings, "controlled_production_executor_enabled", True)
    monkeypatch.setattr(settings, "production_promotion_enabled", False)
    monkeypatch.setattr(settings, "production_rollback_enabled", False)
    monkeypatch.setattr(settings, "production_execution_preview_only", True)
    monkeypatch.setattr(
        settings,
        "production_execution_allowed_project_ids",
        "prj_ci_sacrificial",
    )
    monkeypatch.setattr(
        settings,
        "production_execution_allowed_team_ids",
        "team_ci_sacrificial",
    )
    monkeypatch.setattr(
        settings,
        "production_execution_denied_project_ids",
        REAL_PRODUCTION_PROJECT_ID,
    )
    monkeypatch.setattr(
        settings,
        "vercel_controlled_executor_token",
        "ci-vercel-token",
    )
    monkeypatch.setattr(settings, "preview_acceptance_key", "ci-preview-key")
    monkeypatch.setattr(
        settings,
        "human_production_execution_key",
        "ci-production-execution-key",
    )
    monkeypatch.setattr(settings, "human_approval_key", "ci-approval-key")
    monkeypatch.setattr(settings, "human_release_key", "ci-release-key")
    monkeypatch.setattr(settings, "human_deployment_key", "ci-deployment-key")

    integrity = {
        "allowed": True,
        "integrity_status": "VERIFIED",
        "blocking_reasons": [],
        "acceptance_provenance_tree_sha256": tree,
        "audit_id": audit_id,
        "audit_evidence_sha256": "2" * 64,
        "audit_chain_sha256": "3" * 64,
        "manifest_root_sha256": "4" * 64,
        "chain_head_sha256": "5" * 64,
        "source_commit": "1" * 40,
        "deployment_source_commit": "1" * 40,
        "vercel_deployment_id": "dpl_ci_live_source",
    }
    manifest = {
        "status": "VERIFIED",
        "manifest_root_valid": True,
        "chain_valid": True,
        "manifest_root_sha256": "4" * 64,
        "chain_head_sha256": "5" * 64,
        "entries": {
            audit_id: {
                "status": "VERIFIED",
                "chain_sha256": "5" * 64,
            }
        },
    }

    monkeypatch.setattr(
        release_gate,
        "evaluate_release_integrity",
        lambda *_args, **_kwargs: dict(integrity),
    )
    monkeypatch.setattr(
        deployment_auth,
        "evaluate_release_integrity",
        lambda *_args, **_kwargs: dict(integrity),
    )
    monkeypatch.setattr(
        production_release,
        "evaluate_release_integrity",
        lambda *_args, **_kwargs: dict(integrity),
    )
    monkeypatch.setattr(
        production_release,
        "live_acceptance_integrity_manifest",
        lambda: dict(manifest),
    )
    monkeypatch.setattr(
        prepare_acceptance,
        "_latest_verified_audit",
        lambda: {
            "audit_id": audit_id,
            "acceptance_status": "PASSED",
            "integrity_status": "VERIFIED",
            "live_model_verified": True,
            "tests_passed": True,
            "budget_status": "WITHIN_BUDGET",
            "external_side_effects": "DENY",
            "source_tree_sha256": tree,
        },
    )

    class FakeSacrificialAdapter:
        def __init__(self):
            self.writes = 0
            self.reads = 0
            self.deployment_id = "dpl_ci_step4a_sacrificial"

        def validate_target(self, snapshot):
            if snapshot["target_project_id"] == REAL_PRODUCTION_PROJECT_ID:
                raise RuntimeError(
                    "Controlled Vercel PREPARE target is denylisted"
                )
            assert snapshot["target_project_id"] == "prj_ci_sacrificial"
            assert snapshot["target_team_id"] == "team_ci_sacrificial"
            return (
                snapshot["target_project_id"],
                snapshot["target_team_id"],
            )

        def probe_target(self, snapshot):
            self.validate_target(snapshot)
            return {
                "project_id": snapshot["target_project_id"],
                "team_id": snapshot["target_team_id"],
                "project_name": "ci-sacrificial",
                "provider_write_performed": False,
                "production_traffic_changed": False,
            }

        def build_prepare_request(self, snapshot):
            self.validate_target(snapshot)
            return PreparedDeploymentRequest(
                deployment_id=self.deployment_id,
                project_id=snapshot["target_project_id"],
                team_id=snapshot["target_team_id"],
                body={
                    "target": "production",
                    "autoAssignCustomDomains": False,
                },
                request_sha256="6" * 64,
            )

        def send_prepare(self, request):
            self.writes += 1
            return CandidateDeployment(
                deployment_id=request.deployment_id,
                url="https://ci-sacrificial-step4a.vercel.app",
                state="READY",
                provider_write_performed=True,
                metadata={
                    "provider_result_sha256": "7" * 64,
                    "auto_assign_custom_domains": False,
                    "alias_assigned": False,
                    "production_traffic_changed": False,
                },
            )

        def read_candidate(self, snapshot, deployment_id):
            self.reads += 1
            raise AssertionError("READY path must not require reconciliation")

    fake = FakeSacrificialAdapter()
    monkeypatch.setattr(
        prepare_acceptance,
        "_configured_adapter",
        lambda: fake,
    )

    started = prepare_acceptance.start_prepare_acceptance()

    assert started["acceptance_status"] == "READY_FOR_PROMOTION"
    assert started["sacrificial_project_id"] == "prj_ci_sacrificial"
    assert started["real_project_id"] == REAL_PRODUCTION_PROJECT_ID
    assert started["real_project_denylist_verified"] is True
    assert started["target_lookup_verified"] is True
    assert started["prepare_write_count"] == 1
    assert started["candidate_vercel_deployment_id"] == fake.deployment_id
    assert started["production_traffic_changed"] is False
    assert started["production_promotion_performed"] is False
    assert started["production_rollback_performed"] is False
    assert fake.writes == 1
    assert fake.reads == 0

    execution = production_release.get_production_release_execution(
        started["execution_id"]
    )
    assert execution["execution_status"] == "READY_FOR_PROMOTION"
    assert execution["prepare_write_count"] == 1
    assert execution["production_vercel_deployment_id"] is None
    assert execution["previous_production_deployment_id"] is None
    assert execution["decisions"] == []

    authorized = prepare_acceptance.authorize_prepare_acceptance(
        started["id"]
    )
    assert authorized["acceptance_status"] == "PROMOTE_AUTHORIZED"
    assert authorized["human_decision_id"] is not None
    assert len(authorized["decision_sha256"]) == 64
    assert authorized["production_traffic_changed"] is False
    assert authorized["production_promotion_performed"] is False
    assert authorized["production_rollback_performed"] is False
    assert fake.writes == 1

    final_execution = production_release.get_production_release_execution(
        started["execution_id"]
    )
    assert final_execution["execution_status"] == "READY_FOR_PROMOTION"
    assert len(final_execution["decisions"]) == 1
    assert final_execution["decisions"][0]["decision"] == "PROMOTE"
    assert (
        final_execution["decisions"][0]["provider_write_performed"]
        is False
    )
    assert (
        final_execution["decisions"][0]["production_traffic_changed"]
        is False
    )

    cleaned = prepare_acceptance.cleanup_prepare_acceptance(started["id"])
    assert cleaned["acceptance_status"] == "CLEANED_UP"
    assert cleaned["production_traffic_changed"] is False

    with engine.connect() as db:
        archived = db.execute(text("""
          SELECT archived_at
          FROM release_candidates
          WHERE id=:id
        """), {
            "id": cleaned["release_candidate_id"],
        }).mappings().one()
    assert archived["archived_at"] is not None
