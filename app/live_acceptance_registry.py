import hashlib
import json
import re
from pathlib import Path
from typing import Any

AUDIT_DIR = Path(__file__).resolve().parent.parent / "audits" / "live-acceptance"
MANIFEST_NAME = "manifest.json"
MANIFEST_PATH = AUDIT_DIR / MANIFEST_NAME
GENESIS_SHA256 = "0" * 64
_AUDIT_ID = re.compile(r"^[a-f0-9]{8,64}$", re.IGNORECASE)


class AuditRegistryError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditRegistryError(f"Invalid JSON evidence: {path.name}") from exc
    if not isinstance(payload, dict):
        raise AuditRegistryError(f"JSON evidence must be an object: {path.name}")
    return payload, raw


def _load_file(path: Path) -> dict[str, Any]:
    payload, raw = _load_json(path)
    result = dict(payload)
    result["_evidence"] = {
        "filename": path.name,
        "sha256": _sha256(raw),
        "byte_size": len(raw),
        "registry_backend": "REPOSITORY_JSON",
        "read_only": True,
    }
    return result


def _audit_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [
        path
        for path in sorted(root.glob("*.json"))
        if path.name != MANIFEST_NAME
    ]


def _entry_chain_payload(entry: dict[str, Any]) -> dict[str, Any]:
    keys = (
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
    return {key: entry.get(key) for key in keys}


def _manifest_core(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key != "manifest_root_sha256"
    }


def _load_manifest(root: Path) -> dict[str, Any] | None:
    path = root / MANIFEST_NAME
    if not path.exists():
        return None
    manifest, raw = _load_json(path)
    manifest["_manifest_file"] = {
        "filename": path.name,
        "sha256": _sha256(raw),
        "byte_size": len(raw),
    }
    return manifest


def _verify_manifest(root: Path) -> dict[str, Any]:
    manifest = _load_manifest(root)
    if manifest is None:
        return {
            "status": "ORPHANED",
            "manifest_present": False,
            "manifest_valid": False,
            "manifest_root_valid": False,
            "chain_valid": False,
            "entry_count": 0,
            "verified_count": 0,
            "tampered_count": 0,
            "orphaned_count": len(_audit_paths(root)),
            "chain_head_sha256": None,
            "manifest_root_sha256": None,
            "reasons": ["integrity_manifest_missing"],
            "entries": {},
            "missing_files": [],
        }

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        entries = []

    reasons: list[str] = []
    expected_root = manifest.get("manifest_root_sha256")
    calculated_root = _sha256(_canonical_json(_manifest_core(manifest)))
    manifest_root_valid = (
        isinstance(expected_root, str)
        and expected_root == calculated_root
    )
    if not manifest_root_valid:
        reasons.append("manifest_root_sha256_mismatch")

    entry_count_valid = int(manifest.get("entry_count") or -1) == len(entries)
    if not entry_count_valid:
        reasons.append("manifest_entry_count_mismatch")

    expected_previous = str(manifest.get("genesis_sha256") or GENESIS_SHA256)
    if expected_previous != GENESIS_SHA256:
        reasons.append("manifest_genesis_invalid")

    entry_results: dict[str, dict[str, Any]] = {}
    missing_files: list[str] = []
    chain_valid = True

    for position, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            chain_valid = False
            reasons.append(f"manifest_entry_{position}_invalid")
            continue

        entry = dict(raw_entry)
        filename = str(entry.get("filename") or "")
        audit_id = str(entry.get("audit_id") or "")
        local_reasons: list[str] = []

        if entry.get("previous_chain_sha256") != expected_previous:
            local_reasons.append("previous_chain_sha256_mismatch")

        calculated_chain = _sha256(_canonical_json(_entry_chain_payload(entry)))
        if entry.get("chain_sha256") != calculated_chain:
            local_reasons.append("chain_sha256_mismatch")

        if str(entry.get("deployment_source_commit") or "") != str(
            entry.get("source_commit") or ""
        ):
            local_reasons.append("deployment_source_commit_mismatch")

        path = root / filename if filename else root / "__missing__"
        if not filename or not path.is_file():
            local_reasons.append("evidence_file_missing")
            if filename:
                missing_files.append(filename)
            status = "ORPHANED"
        else:
            record = _load_file(path)
            evidence = record.get("_evidence") or {}
            comparisons = {
                "evidence_sha256": evidence.get("sha256"),
                "audit_id": record.get("audit_id"),
                "occurred_at_utc": record.get("occurred_at_utc"),
                "source_commit": record.get("source_commit"),
                "vercel_deployment_id": record.get("vercel_deployment_id"),
                "source_tree_sha256": record.get("source_tree_sha256"),
            }
            for field, actual in comparisons.items():
                if entry.get(field) != actual:
                    local_reasons.append(f"{field}_mismatch")
            status = "VERIFIED" if not local_reasons else "TAMPERED"

        if local_reasons:
            chain_valid = False

        entry_results[audit_id or f"entry-{position}"] = {
            "position": position,
            "status": status,
            "reasons": local_reasons,
            "filename": filename or None,
            "chain_sha256": entry.get("chain_sha256"),
            "previous_chain_sha256": entry.get("previous_chain_sha256"),
            "evidence_sha256": entry.get("evidence_sha256"),
            "source_commit": entry.get("source_commit"),
            "deployment_source_commit": entry.get("deployment_source_commit"),
            "vercel_deployment_id": entry.get("vercel_deployment_id"),
            "source_tree_sha256": entry.get("source_tree_sha256"),
        }
        expected_previous = str(entry.get("chain_sha256") or calculated_chain)

    manifest_head_valid = manifest.get("chain_head_sha256") == expected_previous
    if not manifest_head_valid:
        chain_valid = False
        reasons.append("manifest_chain_head_mismatch")

    if not manifest_root_valid or not entry_count_valid:
        chain_valid = False

    manifest_filenames = {
        str(entry.get("filename"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("filename")
    }
    unmanifested = [
        path.name for path in _audit_paths(root)
        if path.name not in manifest_filenames
    ]
    for filename in unmanifested:
        try:
            record = _load_file(root / filename)
            audit_id = str(record.get("audit_id") or filename)
        except AuditRegistryError:
            audit_id = filename
        entry_results[audit_id] = {
            "position": None,
            "status": "ORPHANED",
            "reasons": ["evidence_not_in_manifest"],
            "filename": filename,
            "chain_sha256": None,
            "previous_chain_sha256": None,
            "evidence_sha256": None,
            "source_commit": None,
            "deployment_source_commit": None,
            "vercel_deployment_id": None,
            "source_tree_sha256": None,
        }
        chain_valid = False

    if not manifest_root_valid:
        for result in entry_results.values():
            if result["status"] == "VERIFIED":
                result["status"] = "TAMPERED"
                result["reasons"].append("manifest_root_sha256_mismatch")

    verified_count = sum(
        1 for result in entry_results.values()
        if result["status"] == "VERIFIED"
    )
    tampered_count = sum(
        1 for result in entry_results.values()
        if result["status"] == "TAMPERED"
    )
    orphaned_count = sum(
        1 for result in entry_results.values()
        if result["status"] == "ORPHANED"
    )
    if tampered_count:
        status = "TAMPERED"
    elif orphaned_count:
        status = "ORPHANED"
    else:
        status = "VERIFIED"

    return {
        "status": status,
        "manifest_present": True,
        "manifest_valid": manifest_root_valid and entry_count_valid,
        "manifest_root_valid": manifest_root_valid,
        "chain_valid": chain_valid and manifest_head_valid,
        "entry_count": len(entries),
        "verified_count": verified_count,
        "tampered_count": tampered_count,
        "orphaned_count": orphaned_count,
        "chain_head_sha256": manifest.get("chain_head_sha256"),
        "manifest_root_sha256": manifest.get("manifest_root_sha256"),
        "calculated_manifest_root_sha256": calculated_root,
        "manifest_file": manifest.get("_manifest_file"),
        "reasons": reasons,
        "entries": entry_results,
        "missing_files": missing_files,
        "unmanifested_files": unmanifested,
    }


def _records(root: Path = AUDIT_DIR) -> list[dict[str, Any]]:
    integrity = _verify_manifest(root)
    records: list[dict[str, Any]] = []
    for path in _audit_paths(root):
        try:
            record = _load_file(path)
        except AuditRegistryError:
            continue
        audit_id = str(record.get("audit_id") or "")
        record["_integrity"] = integrity["entries"].get(
            audit_id,
            {
                "status": "ORPHANED",
                "reasons": ["evidence_not_in_manifest"],
                "filename": path.name,
            },
        )
        records.append(record)
    records.sort(
        key=lambda item: (
            str(item.get("occurred_at_utc") or ""),
            str(item.get("audit_id") or ""),
        ),
        reverse=True,
    )
    return records


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record.get("_evidence") or {}
    integrity = record.get("_integrity") or {}
    artifacts = record.get("artifacts") or []
    return {
        "audit_id": record.get("audit_id"),
        "occurred_at_utc": record.get("occurred_at_utc"),
        "acceptance_status": record.get("acceptance_status"),
        "phase": record.get("phase"),
        "provider": record.get("provider"),
        "model": record.get("model"),
        "gateway_mode": record.get("gateway_mode"),
        "live_model_verified": bool(record.get("live_model_verified")),
        "budget_status": record.get("budget_status"),
        "gateway_request_count": int(record.get("gateway_request_count") or 0),
        "prompt_tokens": int(record.get("prompt_tokens") or 0),
        "completion_tokens": int(record.get("completion_tokens") or 0),
        "total_tokens": int(record.get("total_tokens") or 0),
        "estimated_cost_usd": float(record.get("estimated_cost_usd") or 0),
        "max_cost_usd": float(record.get("max_cost_usd") or 0),
        "artifact_count": int(record.get("artifact_count") or len(artifacts)),
        "source_tree_sha256": record.get("source_tree_sha256"),
        "source_commit": record.get("source_commit"),
        "vercel_deployment_id": record.get("vercel_deployment_id"),
        "duplicate_requests_observed": int(
            record.get("duplicate_requests_observed") or 0
        ),
        "duplicate_suppression_verified": bool(
            record.get("duplicate_suppression_verified")
        ),
        "tests_passed": bool(record.get("tests_passed")),
        "external_side_effects": record.get("external_side_effects"),
        "release_approved": bool(record.get("release_approved")),
        "evidence_filename": evidence.get("filename"),
        "evidence_sha256": evidence.get("sha256"),
        "evidence_byte_size": evidence.get("byte_size"),
        "registry_backend": evidence.get("registry_backend"),
        "integrity_status": integrity.get("status", "ORPHANED"),
        "integrity_reasons": integrity.get("reasons") or [],
        "chain_sha256": integrity.get("chain_sha256"),
        "previous_chain_sha256": integrity.get("previous_chain_sha256"),
        "deployment_source_commit": integrity.get("deployment_source_commit"),
    }


def list_live_acceptance_audits(
    limit: int = 100,
    *,
    root: Path = AUDIT_DIR,
) -> list[dict[str, Any]]:
    capped = min(max(int(limit), 1), 500)
    return [_summary(item) for item in _records(root)[:capped]]


def get_live_acceptance_audit(
    audit_id: str,
    *,
    root: Path = AUDIT_DIR,
) -> dict[str, Any]:
    value = (audit_id or "").strip()
    if not _AUDIT_ID.fullmatch(value):
        raise LookupError("Live acceptance audit not found")
    for item in _records(root):
        if str(item.get("audit_id") or "").lower() == value.lower():
            return item
    raise LookupError("Live acceptance audit not found")


def live_acceptance_integrity_manifest(
    *,
    root: Path = AUDIT_DIR,
) -> dict[str, Any]:
    result = _verify_manifest(root)
    result["schema_version"] = "integrity-verification-v1"
    result["read_only"] = True
    result["live_invocation_enabled"] = False
    return result


def live_acceptance_evidence_index(
    *,
    root: Path = AUDIT_DIR,
) -> dict[str, Any]:
    records = _records(root)
    summaries = [_summary(item) for item in records]
    integrity = _verify_manifest(root)
    return {
        "registry": "LIVE_ACCEPTANCE_AUDIT_REGISTRY",
        "schema_version": "evidence-index-v2",
        "backend": "REPOSITORY_JSON",
        "read_only": True,
        "live_invocation_enabled": False,
        "record_count": len(summaries),
        "passed_count": sum(
            1 for item in summaries if item["acceptance_status"] == "PASSED"
        ),
        "failed_count": sum(
            1 for item in summaries if item["acceptance_status"] == "FAILED"
        ),
        "integrity_status": integrity["status"],
        "integrity_verified_count": integrity["verified_count"],
        "integrity_tampered_count": integrity["tampered_count"],
        "integrity_orphaned_count": integrity["orphaned_count"],
        "chain_head_sha256": integrity["chain_head_sha256"],
        "manifest_root_sha256": integrity["manifest_root_sha256"],
        "total_gateway_requests": sum(
            item["gateway_request_count"] for item in summaries
        ),
        "total_tokens": sum(item["total_tokens"] for item in summaries),
        "total_estimated_cost_usd": round(
            sum(item["estimated_cost_usd"] for item in summaries), 8
        ),
        "records": summaries,
    }
