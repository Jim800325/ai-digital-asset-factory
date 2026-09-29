import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

GENESIS_SHA256 = "0" * 64
MANIFEST_NAME = "manifest.json"


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def build_manifest(
    root: Path,
    deployment_sources: dict[str, str],
) -> dict[str, Any]:
    records = []
    for path in root.glob("*.json"):
        if path.name == MANIFEST_NAME:
            continue
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{path.name} must contain a JSON object")
        deployment_id = str(payload.get("vercel_deployment_id") or "")
        source_commit = str(payload.get("source_commit") or "")
        deployment_commit = deployment_sources.get(deployment_id)
        if not deployment_id or not deployment_commit:
            raise ValueError(
                f"Independent deployment source commit is required for {path.name}"
            )
        records.append((path, raw, payload, deployment_commit))
    records.sort(
        key=lambda item: (
            str(item[2].get("occurred_at_utc") or ""),
            str(item[2].get("audit_id") or ""),
        )
    )

    previous = GENESIS_SHA256
    entries = []
    for path, raw, payload, deployment_commit in records:
        entry = {
            "audit_id": payload.get("audit_id"),
            "filename": path.name,
            "occurred_at_utc": payload.get("occurred_at_utc"),
            "evidence_sha256": sha256(raw),
            "source_commit": payload.get("source_commit"),
            "vercel_deployment_id": payload.get("vercel_deployment_id"),
            "deployment_source_commit": deployment_commit,
            "source_tree_sha256": payload.get("source_tree_sha256"),
            "previous_chain_sha256": previous,
        }
        entry["chain_sha256"] = sha256(canonical(entry))
        previous = entry["chain_sha256"]
        entries.append(entry)

    core = {
        "schema_version": "live-acceptance-manifest-v1",
        "chain_algorithm": "SHA-256",
        "chain_order": "occurred_at_utc_ascending",
        "genesis_sha256": GENESIS_SHA256,
        "entry_count": len(entries),
        "chain_head_sha256": previous,
        "entries": entries,
    }
    manifest = dict(core)
    manifest["manifest_root_sha256"] = sha256(canonical(core))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the append-only Live Acceptance integrity manifest."
    )
    parser.add_argument(
        "--root",
        default="audits/live-acceptance",
        help="Directory containing audit JSON files.",
    )
    parser.add_argument(
        "--deployment-sources",
        required=True,
        help=(
            "JSON file mapping Vercel deployment IDs to independently verified "
            "Git source commit SHAs."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the checked-in manifest is current without writing.",
    )
    args = parser.parse_args()

    root = Path(args.root)
    sources = load_json(Path(args.deployment_sources))
    manifest = build_manifest(
        root,
        {str(key): str(value) for key, value in sources.items()},
    )
    target = root / MANIFEST_NAME
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != rendered:
            raise SystemExit("integrity manifest is stale or does not match evidence")
        print("integrity manifest verified")
        return 0

    target.write_text(rendered, encoding="utf-8")
    print(
        f"wrote {target} with {manifest['entry_count']} entries; "
        f"head={manifest['chain_head_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
