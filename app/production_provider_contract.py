from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.build_proposals import _current_source_state, _is_build_ready, _proposal_payload
from app.config import settings
from app.db import engine


ASSET_CLASSES = {
    "DATASET_API",
    "INTELLIGENCE_REPORT",
    "MICRO_SAAS_TOOL",
    "TEMPLATE_WORKFLOW",
    "CONTENT_IP",
}
STAGE_KINDS = {"PLAN", "GENERATE", "PACKAGE", "QC", "OTHER"}
RESOURCE_TYPES = {
    "CPU_SECONDS",
    "GPU_SECONDS",
    "MEMORY_GB_SECONDS",
    "STORAGE_GB_HOURS",
    "MODEL_INPUT_TOKENS",
    "MODEL_OUTPUT_TOKENS",
    "NETWORK_BYTES",
    "OTHER",
}
PROVIDER_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
STAGE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


@dataclass(frozen=True)
class ProviderStageSpec:
    key: str
    kind: str = "OTHER"
    depends_on: tuple[str, ...] = ()
    max_attempts: int = 3


@dataclass(frozen=True)
class ProductionProviderSpec:
    provider_key: str
    asset_class: str
    provider_version: str
    stages: tuple[ProviderStageSpec, ...]
    contract_version: str = "v0.1"
    execution_mode: str = "SANDBOX_FIRST"
    external_publish_mode: str = "HUMAN_GATED"
    capabilities: tuple[str, ...] = field(default_factory=tuple)


class ProviderManifestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_kind: str = Field(min_length=1, max_length=100)
    schema_version: str = Field(default="v1", min_length=1, max_length=50)
    payload: dict[str, Any]


class ProductionProvider(Protocol):
    @property
    def spec(self) -> ProductionProviderSpec: ...

    def execute_stage(self, job_id: str, stage_key: str) -> dict: ...


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _validate_spec(spec: ProductionProviderSpec) -> list[dict]:
    if not PROVIDER_KEY_RE.fullmatch(spec.provider_key):
        raise ValueError("provider_key must use lowercase letters, digits, '-' or '_'")
    if spec.asset_class not in ASSET_CLASSES:
        raise ValueError("unsupported asset_class")
    if not spec.provider_version.strip():
        raise ValueError("provider_version is required")
    if spec.execution_mode not in {"SANDBOX_FIRST", "INTERNAL_ONLY"}:
        raise ValueError("unsupported execution_mode")
    if spec.external_publish_mode not in {"DISABLED", "HUMAN_GATED"}:
        raise ValueError("unsupported external_publish_mode")
    if not spec.stages:
        raise ValueError("at least one stage is required")

    seen: set[str] = set()
    graph: list[dict] = []
    for position, stage in enumerate(spec.stages):
        if not STAGE_KEY_RE.fullmatch(stage.key):
            raise ValueError(f"invalid stage key: {stage.key}")
        if stage.key in seen:
            raise ValueError(f"duplicate stage key: {stage.key}")
        if stage.kind not in STAGE_KINDS:
            raise ValueError(f"unsupported stage kind: {stage.kind}")
        if not 1 <= int(stage.max_attempts) <= 20:
            raise ValueError("stage max_attempts must be between 1 and 20")
        missing = [dep for dep in stage.depends_on if dep not in seen]
        if missing:
            raise ValueError(
                f"stage {stage.key} depends on unknown or later stages: {missing}"
            )
        graph.append(
            {
                "key": stage.key,
                "kind": stage.kind,
                "depends_on": list(stage.depends_on),
                "max_attempts": int(stage.max_attempts),
                "position": position,
            }
        )
        seen.add(stage.key)
    return graph


def provider_spec_payload(spec: ProductionProviderSpec) -> dict:
    graph = _validate_spec(spec)
    return {
        "provider_key": spec.provider_key,
        "asset_class": spec.asset_class,
        "provider_version": spec.provider_version,
        "contract_version": spec.contract_version,
        "execution_mode": spec.execution_mode,
        "external_publish_mode": spec.external_publish_mode,
        "capabilities": list(spec.capabilities),
        "stage_graph": graph,
    }


def register_provider_definition(spec: ProductionProviderSpec) -> dict:
    payload = provider_spec_payload(spec)
    spec_sha256 = _sha256(payload)
    with engine.begin() as db:
        provider_id = db.execute(
            text("""
              INSERT INTO production_provider_definitions(
                provider_key,asset_class,provider_version,contract_version,
                execution_mode,external_publish_mode,stage_graph,capabilities,
                spec_sha256,active,updated_at)
              VALUES(
                :provider_key,:asset_class,:provider_version,:contract_version,
                :execution_mode,:external_publish_mode,CAST(:stage_graph AS jsonb),
                CAST(:capabilities AS jsonb),:spec_sha256,true,now())
              ON CONFLICT(provider_key) DO UPDATE SET
                asset_class=excluded.asset_class,
                provider_version=excluded.provider_version,
                contract_version=excluded.contract_version,
                execution_mode=excluded.execution_mode,
                external_publish_mode=excluded.external_publish_mode,
                stage_graph=excluded.stage_graph,
                capabilities=excluded.capabilities,
                spec_sha256=excluded.spec_sha256,
                active=true,
                updated_at=now()
              RETURNING id
            """),
            {
                **{k: v for k, v in payload.items() if k not in {"stage_graph", "capabilities"}},
                "stage_graph": _canonical_json(payload["stage_graph"]),
                "capabilities": _canonical_json(payload["capabilities"]),
                "spec_sha256": spec_sha256,
            },
        ).scalar_one()
    return {
        "provider_id": str(provider_id),
        "provider_key": spec.provider_key,
        "asset_class": spec.asset_class,
        "provider_version": spec.provider_version,
        "contract_version": spec.contract_version,
        "spec_sha256": spec_sha256,
    }


def list_provider_definitions(limit: int = 100, asset_class: str | None = None) -> list[dict]:
    normalized = asset_class.upper().strip() if asset_class else None
    if normalized and normalized not in ASSET_CLASSES:
        raise ValueError("unsupported asset_class")
    sql = """
      SELECT id,provider_key,asset_class,provider_version,contract_version,
             execution_mode,external_publish_mode,stage_graph,capabilities,
             spec_sha256,active,created_at,updated_at
      FROM production_provider_definitions
      WHERE active=true
    """
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
    if normalized:
        sql += " AND asset_class=:asset_class"
        params["asset_class"] = normalized
    sql += " ORDER BY asset_class,provider_key LIMIT :limit"
    with engine.connect() as db:
        return [dict(row) for row in db.execute(text(sql), params).mappings().all()]


def _event(
    db,
    job_id,
    event_type: str,
    *,
    stage_key: str | None = None,
    actor: str = "system",
    payload: dict | None = None,
) -> None:
    db.execute(
        text("""
          INSERT INTO production_provider_events(
            job_id,stage_key,event_type,actor,payload)
          VALUES(
            CAST(:job_id AS uuid),:stage_key,:event_type,:actor,CAST(:payload AS jsonb))
        """),
        {
            "job_id": str(job_id),
            "stage_key": stage_key,
            "event_type": event_type,
            "actor": (actor or "system")[:200],
            "payload": _canonical_json(payload or {}),
        },
    )


def _provider_row(db, provider_key: str):
    return db.execute(
        text("""
          SELECT id,provider_key,asset_class,provider_version,contract_version,
                 execution_mode,external_publish_mode,stage_graph,capabilities,
                 spec_sha256,active
          FROM production_provider_definitions
          WHERE provider_key=:provider_key
          FOR SHARE
        """),
        {"provider_key": provider_key},
    ).mappings().one_or_none()


def _source_is_current(db, job: dict) -> tuple[bool, str]:
    proposal = db.execute(
        text("""
          SELECT id,opportunity_id,revision,proposal_status,source_fingerprint,
                 requires_human_approval,execution_enabled
          FROM build_proposals
          WHERE id=:id
        """),
        {"id": job["build_proposal_id"]},
    ).mappings().one_or_none()
    if proposal is None:
        return False, "build proposal no longer exists"
    if proposal["proposal_status"] != "APPROVED":
        return False, "build proposal is no longer APPROVED"
    if not proposal["requires_human_approval"] or proposal["execution_enabled"]:
        return False, "build proposal safety invariant failed"
    if int(proposal["revision"]) != int(job["proposal_revision"]):
        return False, "build proposal revision changed"
    if proposal["source_fingerprint"] != job["source_fingerprint"]:
        return False, "build proposal fingerprint changed"

    opportunity, report, validation = _current_source_state(
        db, proposal["opportunity_id"]
    )
    if not _is_build_ready(opportunity, report, validation):
        return False, "Product BUILD_READY is no longer current"
    current = _proposal_payload(
        dict(opportunity), dict(report), dict(validation)
    )
    if current["source_fingerprint"] != job["source_fingerprint"]:
        return False, "current opportunity research state changed"
    if opportunity["asset_type"] != job["asset_class"]:
        return False, "asset class changed"
    return True, "CURRENT"


def create_provider_job(
    provider_key: str,
    proposal_id,
    *,
    requested_by: str = "scheduler",
) -> dict:
    if not settings.production_provider_contract_enabled:
        raise RuntimeError("Production Provider Contract is disabled")

    with engine.begin() as db:
        provider = _provider_row(db, provider_key)
        if provider is None or not provider["active"]:
            raise LookupError("Production provider definition not found")

        proposal = db.execute(
            text("""
              SELECT bp.id,bp.opportunity_id,bp.revision,bp.proposal_status,
                     bp.source_fingerprint,bp.requires_human_approval,
                     bp.execution_enabled,bp.artifact_type,o.asset_type,o.title
              FROM build_proposals bp
              JOIN digital_asset_opportunities o ON o.id=bp.opportunity_id
              WHERE bp.id=CAST(:id AS uuid)
              FOR UPDATE OF bp
            """),
            {"id": proposal_id},
        ).mappings().one_or_none()
        if proposal is None:
            raise LookupError("Build proposal not found")
        if proposal["proposal_status"] != "APPROVED":
            raise RuntimeError("Only APPROVED build proposals may create provider jobs")
        if not proposal["requires_human_approval"]:
            raise RuntimeError("Human build approval invariant failed")
        if proposal["execution_enabled"]:
            raise RuntimeError("Production execution must remain disabled")
        if proposal["asset_type"] != provider["asset_class"]:
            raise RuntimeError("Provider asset class does not match build proposal")

        opportunity, report, validation = _current_source_state(
            db, proposal["opportunity_id"]
        )
        if not _is_build_ready(opportunity, report, validation):
            raise RuntimeError("Product BUILD_READY is no longer current")
        current = _proposal_payload(
            dict(opportunity), dict(report), dict(validation)
        )
        if current["source_fingerprint"] != proposal["source_fingerprint"]:
            raise RuntimeError("Build proposal source state changed")

        contract_snapshot = {
            "provider_key": provider["provider_key"],
            "asset_class": provider["asset_class"],
            "provider_version": provider["provider_version"],
            "contract_version": provider["contract_version"],
            "execution_mode": provider["execution_mode"],
            "external_publish_mode": provider["external_publish_mode"],
            "stage_graph": provider["stage_graph"],
            "capabilities": provider["capabilities"],
            "spec_sha256": provider["spec_sha256"],
        }
        input_snapshot = {
            "proposal_id": str(proposal["id"]),
            "proposal_revision": int(proposal["revision"]),
            "opportunity_id": str(proposal["opportunity_id"]),
            "opportunity_title": proposal["title"],
            "asset_class": proposal["asset_type"],
            "artifact_type": proposal["artifact_type"],
            "source_fingerprint": proposal["source_fingerprint"],
        }

        existing = db.execute(
            text("""
              SELECT id,job_status
              FROM production_provider_jobs
              WHERE provider_id=:provider_id
                AND build_proposal_id=:proposal_id
                AND proposal_revision=:revision
                AND source_fingerprint=:fingerprint
              FOR UPDATE
            """),
            {
                "provider_id": provider["id"],
                "proposal_id": proposal["id"],
                "revision": proposal["revision"],
                "fingerprint": proposal["source_fingerprint"],
            },
        ).mappings().one_or_none()
        if existing:
            return {
                "job_id": str(existing["id"]),
                "provider_key": provider_key,
                "job_status": existing["job_status"],
                "created": False,
            }

        job_id = db.execute(
            text("""
              INSERT INTO production_provider_jobs(
                provider_id,build_proposal_id,proposal_revision,
                source_fingerprint,asset_class,job_status,
                contract_snapshot,input_snapshot,requested_by)
              VALUES(
                :provider_id,:proposal_id,:revision,:fingerprint,:asset_class,
                'READY',CAST(:contract_snapshot AS jsonb),
                CAST(:input_snapshot AS jsonb),:requested_by)
              RETURNING id
            """),
            {
                "provider_id": provider["id"],
                "proposal_id": proposal["id"],
                "revision": proposal["revision"],
                "fingerprint": proposal["source_fingerprint"],
                "asset_class": proposal["asset_type"],
                "contract_snapshot": _canonical_json(contract_snapshot),
                "input_snapshot": _canonical_json(input_snapshot),
                "requested_by": (requested_by or "scheduler")[:200],
            },
        ).scalar_one()

        for stage in provider["stage_graph"]:
            db.execute(
                text("""
                  INSERT INTO production_provider_job_stages(
                    job_id,stage_key,stage_kind,position,depends_on,
                    stage_status,attempt_count,max_attempts)
                  VALUES(
                    :job_id,:stage_key,:stage_kind,:position,
                    CAST(:depends_on AS jsonb),'PENDING',0,:max_attempts)
                """),
                {
                    "job_id": job_id,
                    "stage_key": stage["key"],
                    "stage_kind": stage["kind"],
                    "position": stage["position"],
                    "depends_on": _canonical_json(stage["depends_on"]),
                    "max_attempts": stage["max_attempts"],
                },
            )

        _event(
            db,
            job_id,
            "JOB_CREATED",
            actor=requested_by,
            payload={
                "provider_key": provider_key,
                "proposal_id": str(proposal["id"]),
                "proposal_revision": int(proposal["revision"]),
                "source_fingerprint": proposal["source_fingerprint"],
            },
        )

    return {
        "job_id": str(job_id),
        "provider_key": provider_key,
        "job_status": "READY",
        "created": True,
    }


def reconcile_provider_job_source(job_id) -> dict:
    with engine.begin() as db:
        job = db.execute(
            text("""
              SELECT *
              FROM production_provider_jobs
              WHERE id=CAST(:id AS uuid)
              FOR UPDATE
            """),
            {"id": job_id},
        ).mappings().one_or_none()
        if job is None:
            raise LookupError("Production provider job not found")
        valid, reason = _source_is_current(db, job)
        if valid:
            return {"job_id": str(job["id"]), "valid": True, "reason": reason}

        if job["job_status"] != "STALE":
            db.execute(
                text("""
                  UPDATE production_provider_jobs
                  SET job_status='STALE',current_stage=NULL,stale_at=now(),
                      updated_at=now(),error=:reason
                  WHERE id=:id
                """),
                {"id": job["id"], "reason": reason[:4000]},
            )
            db.execute(
                text("""
                  UPDATE production_provider_job_stages
                  SET stage_status='STALE',updated_at=now(),last_error=:reason
                  WHERE job_id=:job_id
                    AND stage_status<>'STALE'
                """),
                {"job_id": job["id"], "reason": reason[:4000]},
            )
            _event(
                db,
                job["id"],
                "JOB_STALE",
                payload={"reason": reason},
            )
        return {"job_id": str(job["id"]), "valid": False, "reason": reason}


def _job_snapshot(job: dict) -> dict:
    snapshot = job["contract_snapshot"]
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    return dict(snapshot)


def _downstream_keys(snapshot: dict, stage_key: str, include_self: bool = False) -> list[str]:
    graph = snapshot.get("stage_graph") or []
    dependents: dict[str, set[str]] = {}
    for stage in graph:
        for dependency in stage.get("depends_on") or []:
            dependents.setdefault(dependency, set()).add(stage["key"])

    found: set[str] = {stage_key} if include_self else set()
    queue = [stage_key]
    while queue:
        current = queue.pop(0)
        for child in sorted(dependents.get(current, ())):
            if child in found:
                continue
            found.add(child)
            queue.append(child)

    positions = {stage["key"]: int(stage["position"]) for stage in graph}
    return sorted(found, key=lambda key: positions.get(key, 9999))


def _stage_dependencies_satisfied(db, job_id, depends_on: list[str]) -> bool:
    if not depends_on:
        return True
    count = int(
        db.execute(
            text("""
              SELECT COUNT(*)
              FROM production_provider_job_stages
              WHERE job_id=:job_id
                AND stage_key=ANY(:depends_on)
                AND stage_status='SUCCEEDED'
            """),
            {"job_id": job_id, "depends_on": depends_on},
        ).scalar_one()
    )
    return count == len(depends_on)


def start_provider_stage(job_id, stage_key: str, *, actor: str = "worker") -> dict:
    source = reconcile_provider_job_source(job_id)
    if not source["valid"]:
        raise RuntimeError(source["reason"])

    with engine.begin() as db:
        job = db.execute(
            text("""
              SELECT *
              FROM production_provider_jobs
              WHERE id=CAST(:id AS uuid)
              FOR UPDATE
            """),
            {"id": job_id},
        ).mappings().one()
        if job["job_status"] in {"FAILED", "BLOCKED", "STALE", "CANCELLED"}:
            raise RuntimeError("Provider job cannot start another stage")

        stage = db.execute(
            text("""
              SELECT *
              FROM production_provider_job_stages
              WHERE job_id=:job_id AND stage_key=:stage_key
              FOR UPDATE
            """),
            {"job_id": job["id"], "stage_key": stage_key},
        ).mappings().one_or_none()
        if stage is None:
            raise LookupError("Provider stage not found")
        if stage["stage_status"] not in {"PENDING", "STALE"}:
            raise RuntimeError("Provider stage is not startable")
        depends_on = stage["depends_on"]
        if isinstance(depends_on, str):
            depends_on = json.loads(depends_on)
        if not _stage_dependencies_satisfied(db, job["id"], list(depends_on)):
            raise RuntimeError("Provider stage dependencies are not satisfied")
        if int(stage["attempt_count"]) >= int(stage["max_attempts"]):
            raise RuntimeError("Provider stage retry budget exhausted")

        attempt = int(stage["attempt_count"]) + 1
        db.execute(
            text("""
              UPDATE production_provider_job_stages
              SET stage_status='RUNNING',attempt_count=:attempt,
                  started_at=now(),finished_at=NULL,updated_at=now(),
                  last_error=NULL
              WHERE id=:id
            """),
            {"id": stage["id"], "attempt": attempt},
        )
        db.execute(
            text("""
              UPDATE production_provider_jobs
              SET job_status='RUNNING',current_stage=:stage_key,
                  started_at=COALESCE(started_at,now()),
                  completed_at=NULL,updated_at=now(),error=NULL
              WHERE id=:id
            """),
            {"id": job["id"], "stage_key": stage_key},
        )
        _event(
            db,
            job["id"],
            "STAGE_STARTED",
            stage_key=stage_key,
            actor=actor,
            payload={"attempt": attempt},
        )
    return {
        "job_id": str(job["id"]),
        "stage_key": stage_key,
        "stage_status": "RUNNING",
        "attempt_count": attempt,
    }


def _invalidate_keys(
    db,
    job: dict,
    keys: list[str],
    *,
    reason: str,
    actor: str,
) -> list[str]:
    if not keys:
        return []
    running = db.execute(
        text("""
          SELECT stage_key
          FROM production_provider_job_stages
          WHERE job_id=:job_id
            AND stage_key=ANY(:keys)
            AND stage_status='RUNNING'
        """),
        {"job_id": job["id"], "keys": keys},
    ).scalars().all()
    if running:
        raise RuntimeError(f"Cannot invalidate running stages: {list(running)}")

    db.execute(
        text("""
          UPDATE production_provider_job_stages
          SET stage_status='STALE',attempt_count=0,
              output_manifest_sha256=NULL,started_at=NULL,finished_at=NULL,
              updated_at=now(),last_error=:reason
          WHERE job_id=:job_id
            AND stage_key=ANY(:keys)
        """),
        {"job_id": job["id"], "keys": keys, "reason": reason[:4000]},
    )
    db.execute(
        text("""
          UPDATE production_provider_manifests
          SET is_current=false,superseded_at=COALESCE(superseded_at,now())
          WHERE job_id=:job_id
            AND stage_key=ANY(:keys)
            AND is_current=true
        """),
        {"job_id": job["id"], "keys": keys},
    )
    db.execute(
        text("""
          UPDATE production_provider_jobs
          SET job_status='READY',current_stage=NULL,completed_at=NULL,
              updated_at=now(),error=NULL
          WHERE id=:id
        """),
        {"id": job["id"]},
    )
    _event(
        db,
        job["id"],
        "DOWNSTREAM_INVALIDATED",
        actor=actor,
        payload={"stages": keys, "reason": reason},
    )
    return keys


def invalidate_provider_stage(
    job_id,
    stage_key: str,
    *,
    reason: str,
    actor: str = "worker",
) -> dict:
    source = reconcile_provider_job_source(job_id)
    if not source["valid"]:
        raise RuntimeError(source["reason"])
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 3:
        raise ValueError("reason must contain at least 3 characters")

    with engine.begin() as db:
        job = db.execute(
            text("""
              SELECT *
              FROM production_provider_jobs
              WHERE id=CAST(:id AS uuid)
              FOR UPDATE
            """),
            {"id": job_id},
        ).mappings().one()
        snapshot = _job_snapshot(job)
        known = {stage["key"] for stage in snapshot.get("stage_graph") or []}
        if stage_key not in known:
            raise LookupError("Provider stage not found")
        keys = _downstream_keys(snapshot, stage_key, include_self=True)
        invalidated = _invalidate_keys(
            db,
            job,
            keys,
            reason=clean_reason,
            actor=actor,
        )
    return {
        "job_id": str(job["id"]),
        "job_status": "READY",
        "invalidated_stages": invalidated,
    }


def write_provider_manifest(
    job_id,
    stage_key: str,
    manifest: ProviderManifestEnvelope,
    *,
    actor: str = "worker",
) -> dict:
    if not isinstance(manifest, ProviderManifestEnvelope):
        manifest = ProviderManifestEnvelope.model_validate(manifest)
    envelope = {
        "manifest_kind": manifest.manifest_kind,
        "schema_version": manifest.schema_version,
        "payload": manifest.payload,
    }
    content_sha256 = _sha256(envelope)

    with engine.begin() as db:
        job = db.execute(
            text("""
              SELECT *
              FROM production_provider_jobs
              WHERE id=CAST(:id AS uuid)
              FOR UPDATE
            """),
            {"id": job_id},
        ).mappings().one_or_none()
        if job is None:
            raise LookupError("Production provider job not found")
        if job["job_status"] in {"STALE", "FAILED", "BLOCKED", "CANCELLED"}:
            raise RuntimeError("Provider job cannot accept a manifest")

        stage = db.execute(
            text("""
              SELECT *
              FROM production_provider_job_stages
              WHERE job_id=:job_id AND stage_key=:stage_key
              FOR UPDATE
            """),
            {"job_id": job["id"], "stage_key": stage_key},
        ).mappings().one_or_none()
        if stage is None:
            raise LookupError("Provider stage not found")
        if stage["stage_status"] != "RUNNING":
            raise RuntimeError("Manifest may only be written by a RUNNING stage")

        latest = db.execute(
            text("""
              SELECT id,manifest_version,content_sha256,is_current
              FROM production_provider_manifests
              WHERE job_id=:job_id
                AND stage_key=:stage_key
                AND manifest_kind=:manifest_kind
              ORDER BY manifest_version DESC
              LIMIT 1
              FOR UPDATE
            """),
            {
                "job_id": job["id"],
                "stage_key": stage_key,
                "manifest_kind": manifest.manifest_kind,
            },
        ).mappings().one_or_none()
        if (
            latest
            and latest["is_current"]
            and latest["content_sha256"] == content_sha256
        ):
            return {
                "job_id": str(job["id"]),
                "stage_key": stage_key,
                "manifest_kind": manifest.manifest_kind,
                "content_sha256": content_sha256,
                "changed": False,
                "invalidated_stages": [],
            }

        version = int(latest["manifest_version"]) + 1 if latest else 1
        if latest and latest["is_current"]:
            db.execute(
                text("""
                  UPDATE production_provider_manifests
                  SET is_current=false,superseded_at=now()
                  WHERE id=:id
                """),
                {"id": latest["id"]},
            )

        db.execute(
            text("""
              INSERT INTO production_provider_manifests(
                job_id,stage_key,manifest_kind,schema_version,
                manifest_version,content,content_sha256,is_current)
              VALUES(
                :job_id,:stage_key,:manifest_kind,:schema_version,
                :manifest_version,CAST(:content AS jsonb),:content_sha256,true)
            """),
            {
                "job_id": job["id"],
                "stage_key": stage_key,
                "manifest_kind": manifest.manifest_kind,
                "schema_version": manifest.schema_version,
                "manifest_version": version,
                "content": _canonical_json(manifest.payload),
                "content_sha256": content_sha256,
            },
        )
        db.execute(
            text("""
              UPDATE production_provider_job_stages
              SET output_manifest_sha256=:sha,updated_at=now()
              WHERE id=:id
            """),
            {"id": stage["id"], "sha": content_sha256},
        )

        invalidated: list[str] = []
        if latest:
            snapshot = _job_snapshot(job)
            downstream = _downstream_keys(snapshot, stage_key)
            invalidated = _invalidate_keys(
                db,
                job,
                downstream,
                reason=f"upstream manifest changed at {stage_key}",
                actor=actor,
            )
            db.execute(
                text("""
                  UPDATE production_provider_jobs
                  SET job_status='RUNNING',current_stage=:stage_key,
                      completed_at=NULL,updated_at=now(),error=NULL
                  WHERE id=:id
                """),
                {"id": job["id"], "stage_key": stage_key},
            )

        _event(
            db,
            job["id"],
            "MANIFEST_WRITTEN",
            stage_key=stage_key,
            actor=actor,
            payload={
                "manifest_kind": manifest.manifest_kind,
                "schema_version": manifest.schema_version,
                "manifest_version": version,
                "content_sha256": content_sha256,
                "changed": True,
            },
        )

    return {
        "job_id": str(job["id"]),
        "stage_key": stage_key,
        "manifest_kind": manifest.manifest_kind,
        "manifest_version": version,
        "content_sha256": content_sha256,
        "changed": True,
        "invalidated_stages": invalidated,
    }


def complete_provider_stage(job_id, stage_key: str, *, actor: str = "worker") -> dict:
    with engine.begin() as db:
        job = db.execute(
            text("""
              SELECT *
              FROM production_provider_jobs
              WHERE id=CAST(:id AS uuid)
              FOR UPDATE
            """),
            {"id": job_id},
        ).mappings().one_or_none()
        if job is None:
            raise LookupError("Production provider job not found")
        stage = db.execute(
            text("""
              SELECT *
              FROM production_provider_job_stages
              WHERE job_id=:job_id AND stage_key=:stage_key
              FOR UPDATE
            """),
            {"job_id": job["id"], "stage_key": stage_key},
        ).mappings().one_or_none()
        if stage is None:
            raise LookupError("Provider stage not found")
        if stage["stage_status"] != "RUNNING":
            raise RuntimeError("Only RUNNING stages may complete")

        db.execute(
            text("""
              UPDATE production_provider_job_stages
              SET stage_status='SUCCEEDED',finished_at=now(),updated_at=now(),
                  last_error=NULL
              WHERE id=:id
            """),
            {"id": stage["id"]},
        )

        stages = db.execute(
            text("""
              SELECT stage_key,stage_kind,stage_status,position
              FROM production_provider_job_stages
              WHERE job_id=:job_id
              ORDER BY position
            """),
            {"job_id": job["id"]},
        ).mappings().all()
        statuses = {
            row["stage_key"]: (
                "SUCCEEDED" if row["stage_key"] == stage_key else row["stage_status"]
            )
            for row in stages
        }
        all_succeeded = all(value == "SUCCEEDED" for value in statuses.values())
        if stage["stage_kind"] == "QC":
            job_status = "QC_PASSED"
        elif all_succeeded and any(row["stage_kind"] == "QC" for row in stages):
            job_status = "QC_PASSED"
        elif all_succeeded:
            job_status = "COMPLETED"
        elif stage["stage_kind"] == "PACKAGE":
            job_status = "ARTIFACT_READY"
        else:
            job_status = "READY"

        db.execute(
            text("""
              UPDATE production_provider_jobs
              SET job_status=:status,current_stage=NULL,
                  completed_at=CASE
                    WHEN :all_succeeded THEN now()
                    ELSE NULL
                  END,
                  updated_at=now(),error=NULL
              WHERE id=:id
            """),
            {
                "id": job["id"],
                "status": job_status,
                "all_succeeded": all_succeeded,
            },
        )
        _event(
            db,
            job["id"],
            "STAGE_SUCCEEDED",
            stage_key=stage_key,
            actor=actor,
            payload={"job_status": job_status},
        )
        if all_succeeded and job_status in {"QC_PASSED", "COMPLETED"}:
            _event(
                db,
                job["id"],
                "JOB_PROVIDER_COMPLETE",
                actor=actor,
                payload={
                    "job_status": job_status,
                    "publish_enabled": False,
                    "production_execution_enabled": False,
                },
            )
    return {
        "job_id": str(job["id"]),
        "stage_key": stage_key,
        "stage_status": "SUCCEEDED",
        "job_status": job_status,
    }


def fail_provider_stage(
    job_id,
    stage_key: str,
    error: str,
    *,
    retryable: bool = True,
    actor: str = "worker",
) -> dict:
    clean_error = (error or "provider stage failed").strip()[:4000]
    with engine.begin() as db:
        job = db.execute(
            text("""
              SELECT *
              FROM production_provider_jobs
              WHERE id=CAST(:id AS uuid)
              FOR UPDATE
            """),
            {"id": job_id},
        ).mappings().one_or_none()
        if job is None:
            raise LookupError("Production provider job not found")
        stage = db.execute(
            text("""
              SELECT *
              FROM production_provider_job_stages
              WHERE job_id=:job_id AND stage_key=:stage_key
              FOR UPDATE
            """),
            {"job_id": job["id"], "stage_key": stage_key},
        ).mappings().one_or_none()
        if stage is None:
            raise LookupError("Provider stage not found")
        if stage["stage_status"] != "RUNNING":
            raise RuntimeError("Only RUNNING stages may fail")

        can_retry = retryable and int(stage["attempt_count"]) < int(stage["max_attempts"])
        stage_status = "WAITING_RETRY" if can_retry else "FAILED"
        job_status = "WAITING_RETRY" if can_retry else "FAILED"
        db.execute(
            text("""
              UPDATE production_provider_job_stages
              SET stage_status=:status,finished_at=now(),updated_at=now(),
                  last_error=:error
              WHERE id=:id
            """),
            {"id": stage["id"], "status": stage_status, "error": clean_error},
        )
        db.execute(
            text("""
              UPDATE production_provider_jobs
              SET job_status=:status,current_stage=NULL,updated_at=now(),error=:error
              WHERE id=:id
            """),
            {"id": job["id"], "status": job_status, "error": clean_error},
        )
        _event(
            db,
            job["id"],
            "STAGE_FAILED",
            stage_key=stage_key,
            actor=actor,
            payload={"retryable": can_retry, "error": clean_error},
        )
    return {
        "job_id": str(job["id"]),
        "stage_key": stage_key,
        "stage_status": stage_status,
        "job_status": job_status,
        "retryable": can_retry,
    }


def retry_provider_stage(job_id, stage_key: str, *, actor: str = "worker") -> dict:
    source = reconcile_provider_job_source(job_id)
    if not source["valid"]:
        raise RuntimeError(source["reason"])

    with engine.begin() as db:
        job = db.execute(
            text("""
              SELECT *
              FROM production_provider_jobs
              WHERE id=CAST(:id AS uuid)
              FOR UPDATE
            """),
            {"id": job_id},
        ).mappings().one()
        stage = db.execute(
            text("""
              SELECT *
              FROM production_provider_job_stages
              WHERE job_id=:job_id AND stage_key=:stage_key
              FOR UPDATE
            """),
            {"job_id": job["id"], "stage_key": stage_key},
        ).mappings().one_or_none()
        if stage is None:
            raise LookupError("Provider stage not found")
        if stage["stage_status"] != "WAITING_RETRY":
            raise RuntimeError("Provider stage is not waiting for retry")
        if int(stage["attempt_count"]) >= int(stage["max_attempts"]):
            raise RuntimeError("Provider stage retry budget exhausted")

        db.execute(
            text("""
              UPDATE production_provider_job_stages
              SET stage_status='PENDING',finished_at=NULL,updated_at=now()
              WHERE id=:id
            """),
            {"id": stage["id"]},
        )
        db.execute(
            text("""
              UPDATE production_provider_jobs
              SET job_status='READY',current_stage=NULL,updated_at=now(),error=NULL
              WHERE id=:id
            """),
            {"id": job["id"]},
        )
        _event(
            db,
            job["id"],
            "RETRY_SCHEDULED",
            stage_key=stage_key,
            actor=actor,
            payload={"next_attempt": int(stage["attempt_count"]) + 1},
        )
    return {
        "job_id": str(job["id"]),
        "stage_key": stage_key,
        "stage_status": "PENDING",
        "job_status": "READY",
    }


def record_provider_resource_usage(
    job_id,
    *,
    resource_type: str,
    quantity: float | Decimal,
    unit: str,
    estimated_cost_usd: float | Decimal = 0,
    stage_key: str | None = None,
    metadata: dict | None = None,
    actor: str = "worker",
) -> dict:
    normalized = resource_type.upper().strip()
    if normalized not in RESOURCE_TYPES:
        raise ValueError("unsupported resource_type")
    q = Decimal(str(quantity))
    cost = Decimal(str(estimated_cost_usd))
    if q < 0 or cost < 0:
        raise ValueError("resource quantity and estimated cost must be non-negative")
    if not (unit or "").strip():
        raise ValueError("unit is required")

    with engine.begin() as db:
        job = db.execute(
            text("""
              SELECT id
              FROM production_provider_jobs
              WHERE id=CAST(:id AS uuid)
            """),
            {"id": job_id},
        ).mappings().one_or_none()
        if job is None:
            raise LookupError("Production provider job not found")
        if stage_key:
            exists = db.execute(
                text("""
                  SELECT 1
                  FROM production_provider_job_stages
                  WHERE job_id=:job_id AND stage_key=:stage_key
                """),
                {"job_id": job["id"], "stage_key": stage_key},
            ).scalar_one_or_none()
            if exists is None:
                raise LookupError("Provider stage not found")

        event_id = db.execute(
            text("""
              INSERT INTO production_provider_resource_events(
                job_id,stage_key,resource_type,quantity,unit,
                estimated_cost_usd,metadata)
              VALUES(
                :job_id,:stage_key,:resource_type,:quantity,:unit,
                :estimated_cost_usd,CAST(:metadata AS jsonb))
              RETURNING id
            """),
            {
                "job_id": job["id"],
                "stage_key": stage_key,
                "resource_type": normalized,
                "quantity": q,
                "unit": unit.strip()[:100],
                "estimated_cost_usd": cost,
                "metadata": _canonical_json(metadata or {}),
            },
        ).scalar_one()
        total_cost = db.execute(
            text("""
              SELECT COALESCE(SUM(estimated_cost_usd),0)
              FROM production_provider_resource_events
              WHERE job_id=:job_id
            """),
            {"job_id": job["id"]},
        ).scalar_one()
        _event(
            db,
            job["id"],
            "RESOURCE_RECORDED",
            stage_key=stage_key,
            actor=actor,
            payload={
                "resource_type": normalized,
                "quantity": str(q),
                "unit": unit.strip(),
                "estimated_cost_usd": str(cost),
                "total_estimated_cost_usd": str(total_cost),
            },
        )
    return {
        "resource_event_id": str(event_id),
        "job_id": str(job["id"]),
        "resource_type": normalized,
        "estimated_cost_usd": float(cost),
        "total_estimated_cost_usd": float(total_cost),
    }


def get_provider_job(job_id) -> dict:
    with engine.connect() as db:
        job = db.execute(
            text("""
              SELECT j.*,p.provider_key,p.provider_version,p.contract_version
              FROM production_provider_jobs j
              JOIN production_provider_definitions p ON p.id=j.provider_id
              WHERE j.id=CAST(:id AS uuid)
            """),
            {"id": job_id},
        ).mappings().one_or_none()
        if job is None:
            raise LookupError("Production provider job not found")
        stages = [
            dict(row)
            for row in db.execute(
                text("""
                  SELECT stage_key,stage_kind,position,depends_on,stage_status,
                         attempt_count,max_attempts,output_manifest_sha256,
                         started_at,finished_at,last_error
                  FROM production_provider_job_stages
                  WHERE job_id=:job_id
                  ORDER BY position
                """),
                {"job_id": job["id"]},
            ).mappings().all()
        ]
        manifests = [
            dict(row)
            for row in db.execute(
                text("""
                  SELECT stage_key,manifest_kind,schema_version,manifest_version,
                         content_sha256,created_at
                  FROM production_provider_manifests
                  WHERE job_id=:job_id AND is_current=true
                  ORDER BY stage_key,manifest_kind
                """),
                {"job_id": job["id"]},
            ).mappings().all()
        ]
        usage = db.execute(
            text("""
              SELECT COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd,
                     COUNT(*) AS resource_events
              FROM production_provider_resource_events
              WHERE job_id=:job_id
            """),
            {"job_id": job["id"]},
        ).mappings().one()
    result = dict(job)
    result["stages"] = stages
    result["current_manifests"] = manifests
    result["resource_events"] = int(usage["resource_events"])
    result["estimated_cost_usd"] = float(usage["estimated_cost_usd"])
    return result


def list_provider_jobs(
    limit: int = 100,
    status: str | None = None,
    provider_key: str | None = None,
) -> list[dict]:
    allowed_statuses = {
        "READY","RUNNING","WAITING_RETRY","ARTIFACT_READY","QC_PASSED",
        "COMPLETED","FAILED","BLOCKED","STALE","CANCELLED",
    }
    normalized_status = status.upper().strip() if status else None
    if normalized_status and normalized_status not in allowed_statuses:
        raise ValueError("unsupported provider job status")

    sql = """
      SELECT j.id,p.provider_key,j.build_proposal_id,j.proposal_revision,
             j.asset_class,j.job_status,j.current_stage,
             j.external_side_effects,j.production_execution_enabled,
             j.publish_enabled,j.requested_by,j.created_at,j.started_at,
             j.completed_at,j.stale_at,j.updated_at,j.error
      FROM production_provider_jobs j
      JOIN production_provider_definitions p ON p.id=j.provider_id
      WHERE 1=1
    """
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
    if normalized_status:
        sql += " AND j.job_status=:status"
        params["status"] = normalized_status
    if provider_key:
        sql += " AND p.provider_key=:provider_key"
        params["provider_key"] = provider_key
    sql += " ORDER BY j.updated_at DESC LIMIT :limit"
    with engine.connect() as db:
        return [dict(row) for row in db.execute(text(sql), params).mappings().all()]
