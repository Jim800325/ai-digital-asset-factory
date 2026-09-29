import hashlib
import json

import pytest

from app.live_acceptance_registry import (
    get_live_acceptance_audit,
    list_live_acceptance_audits,
    live_acceptance_evidence_index,
    live_acceptance_integrity_manifest,
)


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write(root, name, payload):
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _build_manifest(root, records):
    previous = "0" * 64
    entries = []
    for filename, payload in sorted(
        records,
        key=lambda item: item[1]["occurred_at_utc"],
    ):
        raw = (root / filename).read_bytes()
        entry = {
            "audit_id": payload["audit_id"],
            "filename": filename,
            "occurred_at_utc": payload["occurred_at_utc"],
            "evidence_sha256": hashlib.sha256(raw).hexdigest(),
            "source_commit": payload.get("source_commit"),
            "vercel_deployment_id": payload.get("vercel_deployment_id"),
            "deployment_source_commit": payload.get("source_commit"),
            "source_tree_sha256": payload.get("source_tree_sha256"),
            "previous_chain_sha256": previous,
        }
        entry["chain_sha256"] = hashlib.sha256(
            _canonical(entry)
        ).hexdigest()
        previous = entry["chain_sha256"]
        entries.append(entry)

    core = {
        "schema_version": "live-acceptance-manifest-v1",
        "chain_algorithm": "SHA-256",
        "chain_order": "occurred_at_utc_ascending",
        "genesis_sha256": "0" * 64,
        "entry_count": len(entries),
        "chain_head_sha256": previous,
        "entries": entries,
    }
    manifest = dict(core)
    manifest["manifest_root_sha256"] = hashlib.sha256(
        _canonical(core)
    ).hexdigest()
    _write(root, "manifest.json", manifest)
    return manifest


def _sample_records(root):
    older = {
        "audit_id": "aaaaaaaa11111111",
        "occurred_at_utc": "2026-09-29T01:00:00Z",
        "acceptance_status": "FAILED",
        "provider": "AIHUBMIX",
        "model": "gpt-5.6-luna",
        "gateway_request_count": 1,
        "total_tokens": 100,
        "estimated_cost_usd": 0.001,
        "source_commit": "1" * 40,
        "vercel_deployment_id": "dpl_old",
        "source_tree_sha256": None,
    }
    newer = {
        "audit_id": "bbbbbbbb22222222",
        "occurred_at_utc": "2026-09-29T02:00:00Z",
        "acceptance_status": "PASSED",
        "provider": "AIHUBMIX",
        "model": "gpt-5.6-luna",
        "gateway_request_count": 2,
        "total_tokens": 300,
        "estimated_cost_usd": 0.003,
        "source_commit": "2" * 40,
        "vercel_deployment_id": "dpl_new",
        "source_tree_sha256": "a" * 64,
        "artifacts": [{"relative_path": "main.py", "sha256": "f" * 64}],
    }
    _write(root, "older.json", older)
    _write(root, "newer.json", newer)
    _build_manifest(root, [("older.json", older), ("newer.json", newer)])
    return older, newer


def test_registry_lists_aggregates_and_verifies_chain(tmp_path):
    root = tmp_path / "audits"
    _sample_records(root)

    rows = list_live_acceptance_audits(root=root)
    assert [row["audit_id"] for row in rows] == [
        "bbbbbbbb22222222",
        "aaaaaaaa11111111",
    ]
    assert all(row["integrity_status"] == "VERIFIED" for row in rows)

    index = live_acceptance_evidence_index(root=root)
    assert index["read_only"] is True
    assert index["live_invocation_enabled"] is False
    assert index["record_count"] == 2
    assert index["passed_count"] == 1
    assert index["failed_count"] == 1
    assert index["total_gateway_requests"] == 3
    assert index["total_tokens"] == 400
    assert index["total_estimated_cost_usd"] == pytest.approx(0.004)
    assert index["integrity_status"] == "VERIFIED"
    assert index["integrity_verified_count"] == 2
    assert index["integrity_tampered_count"] == 0
    assert index["integrity_orphaned_count"] == 0

    integrity = live_acceptance_integrity_manifest(root=root)
    assert integrity["manifest_root_valid"] is True
    assert integrity["chain_valid"] is True
    assert integrity["status"] == "VERIFIED"


def test_registry_detects_tampered_evidence(tmp_path):
    root = tmp_path / "audits"
    _, newer = _sample_records(root)

    newer["total_tokens"] = 999
    _write(root, "newer.json", newer)

    rows = list_live_acceptance_audits(root=root)
    changed = next(row for row in rows if row["audit_id"] == newer["audit_id"])
    assert changed["integrity_status"] == "TAMPERED"
    assert "evidence_sha256_mismatch" in changed["integrity_reasons"]

    integrity = live_acceptance_integrity_manifest(root=root)
    assert integrity["status"] == "TAMPERED"
    assert integrity["tampered_count"] == 1


def test_registry_detects_orphaned_files_and_missing_evidence(tmp_path):
    root = tmp_path / "audits"
    older, _ = _sample_records(root)

    (root / "older.json").unlink()
    orphan = {
        "audit_id": "cccccccc33333333",
        "occurred_at_utc": "2026-09-29T03:00:00Z",
        "acceptance_status": "FAILED",
    }
    _write(root, "unmanifested.json", orphan)

    rows = list_live_acceptance_audits(root=root)
    extra = next(row for row in rows if row["audit_id"] == orphan["audit_id"])
    assert extra["integrity_status"] == "ORPHANED"
    assert "evidence_not_in_manifest" in extra["integrity_reasons"]

    integrity = live_acceptance_integrity_manifest(root=root)
    assert integrity["status"] == "ORPHANED"
    assert "older.json" in integrity["missing_files"]
    assert "unmanifested.json" in integrity["unmanifested_files"]
    assert integrity["orphaned_count"] == 2


def test_registry_detects_deployment_source_mismatch(tmp_path):
    root = tmp_path / "audits"
    _sample_records(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0]["deployment_source_commit"] = "9" * 40

    entry = manifest["entries"][0]
    chain_payload = {
        key: entry.get(key)
        for key in (
            "audit_id",
            "filename",
            "occurred_at_utc",
            "evidence_sha256",
            "source_commit",
            "vercel_deployment_id",
            "deployment_source_commit",
            "source_tree_sha256",
            "previous_chain_sha256",
        )
    }
    entry["chain_sha256"] = hashlib.sha256(
        _canonical(chain_payload)
    ).hexdigest()
    manifest["entries"][1]["previous_chain_sha256"] = entry["chain_sha256"]
    second = manifest["entries"][1]
    second_payload = {
        key: second.get(key)
        for key in chain_payload
    }
    second["chain_sha256"] = hashlib.sha256(
        _canonical(second_payload)
    ).hexdigest()
    manifest["chain_head_sha256"] = second["chain_sha256"]

    core = {
        key: value
        for key, value in manifest.items()
        if key != "manifest_root_sha256"
    }
    manifest["manifest_root_sha256"] = hashlib.sha256(
        _canonical(core)
    ).hexdigest()
    _write(root, "manifest.json", manifest)

    integrity = live_acceptance_integrity_manifest(root=root)
    assert integrity["status"] == "TAMPERED"
    first = integrity["entries"]["aaaaaaaa11111111"]
    assert "deployment_source_commit_mismatch" in first["reasons"]


def test_registry_detail_is_exact_id_and_preserves_artifacts(tmp_path):
    root = tmp_path / "audits"
    _, newer = _sample_records(root)

    row = get_live_acceptance_audit(newer["audit_id"], root=root)
    assert row["acceptance_status"] == "PASSED"
    assert row["artifacts"][0]["relative_path"] == "main.py"
    assert row["_evidence"]["read_only"] is True
    assert row["_integrity"]["status"] == "VERIFIED"

    with pytest.raises(LookupError):
        get_live_acceptance_audit("../pass.json", root=root)
    with pytest.raises(LookupError):
        get_live_acceptance_audit("deadbeef", root=root)
