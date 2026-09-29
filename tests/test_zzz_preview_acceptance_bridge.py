import hashlib
import json

import pytest
from sqlalchemy import text

from app import preview_acceptance_bridge as bridge
from app.db import engine


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
