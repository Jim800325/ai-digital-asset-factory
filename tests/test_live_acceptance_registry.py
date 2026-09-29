import json

import pytest

from app.live_acceptance_registry import (
    get_live_acceptance_audit,
    list_live_acceptance_audits,
    live_acceptance_evidence_index,
)


def _write(root, name, payload):
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(payload), encoding="utf-8")


def test_registry_lists_and_aggregates_repository_json(tmp_path):
    root = tmp_path / "audits"
    _write(root, "older.json", {
        "audit_id": "aaaaaaaa11111111",
        "occurred_at_utc": "2026-09-29T01:00:00Z",
        "acceptance_status": "FAILED",
        "provider": "AIHUBMIX",
        "model": "gpt-5.6-luna",
        "gateway_request_count": 1,
        "total_tokens": 100,
        "estimated_cost_usd": 0.001,
    })
    _write(root, "newer.json", {
        "audit_id": "bbbbbbbb22222222",
        "occurred_at_utc": "2026-09-29T02:00:00Z",
        "acceptance_status": "PASSED",
        "provider": "AIHUBMIX",
        "model": "gpt-5.6-luna",
        "gateway_request_count": 2,
        "total_tokens": 300,
        "estimated_cost_usd": 0.003,
        "artifacts": [{"relative_path": "main.py", "sha256": "f" * 64}],
    })

    rows = list_live_acceptance_audits(root=root)
    assert [row["audit_id"] for row in rows] == [
        "bbbbbbbb22222222",
        "aaaaaaaa11111111",
    ]
    assert rows[0]["registry_backend"] == "REPOSITORY_JSON"
    assert len(rows[0]["evidence_sha256"]) == 64

    index = live_acceptance_evidence_index(root=root)
    assert index["read_only"] is True
    assert index["live_invocation_enabled"] is False
    assert index["record_count"] == 2
    assert index["passed_count"] == 1
    assert index["failed_count"] == 1
    assert index["total_gateway_requests"] == 3
    assert index["total_tokens"] == 400
    assert index["total_estimated_cost_usd"] == pytest.approx(0.004)


def test_registry_detail_is_exact_id_and_preserves_artifacts(tmp_path):
    root = tmp_path / "audits"
    _write(root, "pass.json", {
        "audit_id": "8b385170ee00c667",
        "occurred_at_utc": "2026-09-29T03:30:12Z",
        "acceptance_status": "PASSED",
        "artifacts": [
            {
                "relative_path": "main.py",
                "byte_size": 169,
                "sha256": "7" * 64,
            }
        ],
    })

    row = get_live_acceptance_audit("8b385170ee00c667", root=root)
    assert row["acceptance_status"] == "PASSED"
    assert row["artifacts"][0]["relative_path"] == "main.py"
    assert row["_evidence"]["read_only"] is True

    with pytest.raises(LookupError):
        get_live_acceptance_audit("../pass.json", root=root)
    with pytest.raises(LookupError):
        get_live_acceptance_audit("deadbeef", root=root)
