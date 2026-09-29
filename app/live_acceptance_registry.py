import hashlib
import json
import re
from pathlib import Path
from typing import Any

AUDIT_DIR = Path(__file__).resolve().parent.parent / "audits" / "live-acceptance"
_AUDIT_ID = re.compile(r"^[a-f0-9]{8,64}$", re.IGNORECASE)


class AuditRegistryError(RuntimeError):
    pass


def _load_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditRegistryError(f"Invalid audit record: {path.name}") from exc
    if not isinstance(payload, dict):
        raise AuditRegistryError(f"Audit record must be a JSON object: {path.name}")

    result = dict(payload)
    result["_evidence"] = {
        "filename": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "byte_size": len(raw),
        "registry_backend": "REPOSITORY_JSON",
        "read_only": True,
    }
    return result


def _records(root: Path = AUDIT_DIR) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            records.append(_load_file(path))
        except AuditRegistryError:
            continue
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


def live_acceptance_evidence_index(
    *,
    root: Path = AUDIT_DIR,
) -> dict[str, Any]:
    records = _records(root)
    summaries = [_summary(item) for item in records]
    return {
        "registry": "LIVE_ACCEPTANCE_AUDIT_REGISTRY",
        "schema_version": "evidence-index-v1",
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
        "total_gateway_requests": sum(
            item["gateway_request_count"] for item in summaries
        ),
        "total_tokens": sum(item["total_tokens"] for item in summaries),
        "total_estimated_cost_usd": round(
            sum(item["estimated_cost_usd"] for item in summaries), 8
        ),
        "records": summaries,
    }
