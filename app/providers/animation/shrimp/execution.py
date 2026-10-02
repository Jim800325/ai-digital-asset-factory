from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text

from app.db import engine
from app.production_provider_contract import (
    ProviderManifestEnvelope,
    complete_provider_stage,
    fail_provider_stage,
    get_provider_job,
    record_provider_resource_usage,
    retry_provider_stage,
    start_provider_stage,
    write_provider_manifest,
)
from app.providers.animation.artifact_verification import (
    AdapterArtifactPayload,
    ArtifactByteResolver,
    verify_artifact_payload,
)
from app.providers.animation.asset_registry import (
    load_asset_registry_snapshot,
    load_voice_registry_snapshot,
)
from app.providers.animation.models import (
    ArtifactProvenance,
    AssetArtifactManifest,
    AssetPlan,
    ContentBrief,
    VerifiedArtifact,
    VoiceArtifactManifest,
    VoicePlan,
    canonical_json,
    manifest_sha256,
)
from app.providers.animation.shrimp.adapters.base import (
    AssetExecutionAdapter,
    VoiceExecutionAdapter,
)


def _current_resource_plan(job_id, plan_kind: str) -> tuple[dict, str]:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT content,content_sha256
              FROM shrimp_animation_resource_plans
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND plan_kind=:plan_kind
                AND plan_status='CURRENT'
              ORDER BY plan_version DESC
              LIMIT 1
            """),
            {"job_id": job_id, "plan_kind": plan_kind},
        ).mappings().one_or_none()
    if row is None:
        raise RuntimeError(f"Current {plan_kind} resource plan is missing")
    return row["content"], row["content_sha256"]


def _current_brief(job_id) -> ContentBrief:
    with engine.connect() as db:
        payload = db.execute(
            text("""
              SELECT content
              FROM production_provider_manifests
              WHERE job_id=CAST(:job_id AS uuid)
                AND stage_key='CONTENT_BRIEF'
                AND manifest_kind='content_brief'
                AND is_current=true
              ORDER BY manifest_version DESC
              LIMIT 1
            """),
            {"job_id": job_id},
        ).scalar_one_or_none()
    if payload is None:
        raise RuntimeError("Current content brief manifest is missing")
    return ContentBrief.model_validate(payload)


def _stage_state(job: dict, stage_key: str) -> str:
    return next(
        row["stage_status"]
        for row in job["stages"]
        if row["stage_key"] == stage_key
    )


def _existing_stage_manifest(job_id, stage_key: str, manifest_kind: str):
    with engine.connect() as db:
        return db.execute(
            text("""
              SELECT content,content_sha256
              FROM production_provider_manifests
              WHERE job_id=CAST(:job_id AS uuid)
                AND stage_key=:stage_key
                AND manifest_kind=:manifest_kind
                AND is_current=true
              ORDER BY manifest_version DESC
              LIMIT 1
            """),
            {
                "job_id": job_id,
                "stage_key": stage_key,
                "manifest_kind": manifest_kind,
            },
        ).mappings().one_or_none()


def _prepare_stage(job_id, stage_key: str, *, actor: str) -> str:
    job = get_provider_job(job_id)
    state = _stage_state(job, stage_key)
    if state == "SUCCEEDED":
        return state
    if state == "WAITING_RETRY":
        retry_provider_stage(job_id, stage_key, actor=actor)
        state = "PENDING"
    if state not in {"PENDING", "STALE"}:
        raise RuntimeError(f"{stage_key} cannot execute from {state}")
    start_provider_stage(job_id, stage_key, actor=actor)
    return "RUNNING"


def _retryable(exc: Exception) -> bool:
    return not isinstance(
        exc,
        (ValueError, PermissionError, LookupError),
    )


def _persist_verified_artifact(
    job_id,
    stage_key: str,
    artifact: VerifiedArtifact,
) -> dict:
    with engine.begin() as db:
        existing = db.execute(
            text("""
              SELECT id,sha256,plan_sha256
              FROM shrimp_animation_artifacts
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND stage_key=:stage_key
                AND logical_key=:logical_key
                AND is_current=true
              FOR UPDATE
            """),
            {
                "job_id": job_id,
                "stage_key": stage_key,
                "logical_key": artifact.logical_key,
            },
        ).mappings().one_or_none()
        if (
            existing
            and existing["sha256"] == artifact.sha256
            and existing["plan_sha256"] == artifact.plan_sha256
        ):
            return {
                "artifact_id": str(existing["id"]),
                "changed": False,
            }
        if existing:
            db.execute(
                text("""
                  UPDATE shrimp_animation_artifacts
                  SET is_current=false,superseded_at=now()
                  WHERE id=:id
                """),
                {"id": existing["id"]},
            )
        artifact_id = db.execute(
            text("""
              INSERT INTO shrimp_animation_artifacts(
                provider_job_id,stage_key,logical_key,artifact_kind,
                source_mode,plan_sha256,storage_uri,media_type,byte_size,
                sha256,license_id,usage_rights,adapter_key,adapter_version,
                provider_request_id,provenance,duration_ms,
                verification_status,is_current)
              VALUES(
                CAST(:job_id AS uuid),:stage_key,:logical_key,:artifact_kind,
                :source_mode,:plan_sha256,:storage_uri,:media_type,:byte_size,
                :sha256,:license_id,:usage_rights,:adapter_key,:adapter_version,
                :provider_request_id,CAST(:provenance AS jsonb),:duration_ms,
                'VERIFIED',true)
              RETURNING id
            """),
            {
                "job_id": job_id,
                "stage_key": stage_key,
                "logical_key": artifact.logical_key,
                "artifact_kind": artifact.artifact_kind,
                "source_mode": artifact.source_mode,
                "plan_sha256": artifact.plan_sha256,
                "storage_uri": artifact.storage_uri,
                "media_type": artifact.media_type,
                "byte_size": artifact.byte_size,
                "sha256": artifact.sha256,
                "license_id": artifact.license_id,
                "usage_rights": artifact.usage_rights,
                "adapter_key": artifact.provenance.adapter_key,
                "adapter_version": artifact.provenance.adapter_version,
                "provider_request_id": artifact.provenance.provider_request_id,
                "provenance": canonical_json(
                    artifact.provenance.model_dump(mode="json")
                ),
                "duration_ms": artifact.duration_ms,
            },
        ).scalar_one()
    return {"artifact_id": str(artifact_id), "changed": True}


def _load_current_verified(
    job_id,
    stage_key: str,
    logical_key: str,
    plan_sha256: str,
) -> VerifiedArtifact | None:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT logical_key,artifact_kind,source_mode,plan_sha256,
                     storage_uri,media_type,byte_size,sha256,license_id,
                     usage_rights,provenance,duration_ms
              FROM shrimp_animation_artifacts
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND stage_key=:stage_key
                AND logical_key=:logical_key
                AND plan_sha256=:plan_sha256
                AND verification_status='VERIFIED'
                AND is_current=true
            """),
            {
                "job_id": job_id,
                "stage_key": stage_key,
                "logical_key": logical_key,
                "plan_sha256": plan_sha256,
            },
        ).mappings().one_or_none()
    if row is None:
        return None
    return VerifiedArtifact.model_validate(dict(row))


def _reuse_payload(requirement, resolver: ArtifactByteResolver) -> AdapterArtifactPayload:
    asset = requirement.reusable_asset
    if asset is None:
        raise RuntimeError("REUSE_READY requirement has no reusable asset")
    content = resolver.read_bytes(asset.storage_uri)
    request_sha = hashlib.sha256(
        canonical_json(asset.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()
    return AdapterArtifactPayload(
        logical_key=requirement.requirement_key,
        artifact_kind=(
            "BACKGROUND"
            if requirement.asset_kind == "BACKGROUND"
            else "CHARACTER"
        ),
        content=content,
        storage_uri=asset.storage_uri,
        media_type=asset.media_type,
        license_id=asset.license_id,
        usage_rights=asset.usage_rights,
        claimed_sha256=asset.sha256,
        provenance=ArtifactProvenance(
            adapter_key="REUSABLE_REGISTRY",
            adapter_version="v0.1",
            provider_request_id=None,
            source_reference=asset.storage_uri,
            source_type="REUSABLE_REGISTRY",
            provenance=asset.provenance,
            request_sha256=request_sha,
        ),
    )


def _asset_prompt(brief: ContentBrief, requirement) -> str:
    character = next(
        (
            item
            for item in brief.characters
            if item.character_id == requirement.logical_id
        ),
        None,
    )
    style = ", ".join(brief.style_tags) or "2d story animation"
    if requirement.asset_kind == "CHARACTER":
        identity = (
            f"{character.display_name}, {character.role}"
            if character
            else requirement.logical_id
        )
        return (
            f"Reusable 2D animation character asset: {identity}; "
            f"variant={requirement.variant or 'idle'}; "
            f"style={style}; transparent background; consistent proportions."
        )
    return (
        f"Reusable 2D animation background: {requirement.logical_id}; "
        f"episode={brief.title}; style={style}; no text; no watermark."
    )


def _asset_seed(job_id, logical_key: str, plan_sha256: str) -> int:
    digest = hashlib.sha256(
        f"{job_id}\n{logical_key}\n{plan_sha256}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") % (2**63 - 1)


def execute_asset_stage(
    job_id,
    *,
    adapter: AssetExecutionAdapter,
    resolver: ArtifactByteResolver,
    actor: str = "shrimp-assets-worker",
) -> dict:
    content, plan_sha = _current_resource_plan(job_id, "ASSET")
    plan = AssetPlan.model_validate(content)
    if not plan.ready_for_asset_execution:
        raise PermissionError("ASSET resource plan is not execution-ready")

    character_ids = sorted({
        item.logical_id
        for item in plan.requirements
        if item.asset_kind == "CHARACTER"
    })
    background_ids = sorted({
        item.logical_id
        for item in plan.requirements
        if item.asset_kind == "BACKGROUND"
    })
    action_keys = sorted({
        item.capability_key
        for item in plan.capabilities
        if item.capability_kind == "ACTION"
    })
    camera_keys = sorted({
        item.capability_key
        for item in plan.capabilities
        if item.capability_kind == "CAMERA"
    })
    snapshot = load_asset_registry_snapshot(
        character_ids=character_ids,
        background_ids=background_ids,
        action_keys=action_keys,
        camera_keys=camera_keys,
    )
    if snapshot["snapshot_sha256"] != plan.registry_snapshot_sha256:
        raise RuntimeError("ASSET registry changed; rerun resource planning")

    job = get_provider_job(job_id)
    if _stage_state(job, "ASSETS") == "SUCCEEDED":
        existing = _existing_stage_manifest(
            job_id,
            "ASSETS",
            "asset_artifact_manifest",
        )
        if existing is None:
            raise RuntimeError("ASSETS succeeded without artifact manifest")
        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "manifest_sha256": existing["content_sha256"],
            "replayed": True,
        }

    brief = _current_brief(job_id)
    _prepare_stage(job_id, "ASSETS", actor=actor)
    artifacts: list[VerifiedArtifact] = []
    try:
        for requirement in plan.requirements:
            cached = _load_current_verified(
                job_id,
                "ASSETS",
                requirement.requirement_key,
                plan_sha,
            )
            if cached is not None:
                artifacts.append(cached)
                continue
            if requirement.status == "REUSE_READY":
                payload = _reuse_payload(requirement, resolver)
                source_mode = "REUSED"
            elif requirement.status == "GENERATION_REQUIRED":
                payload = adapter.generate(
                    requirement,
                    prompt=_asset_prompt(brief, requirement),
                    seed=_asset_seed(
                        job_id,
                        requirement.requirement_key,
                        plan_sha,
                    ),
                    output_prefix=(
                        f"{brief.episode_id}-"
                        f"{requirement.requirement_key.replace(':','-')}"
                    ),
                )
                source_mode = "GENERATED"
            else:
                raise PermissionError(
                    f"Asset requirement is not executable: {requirement.status}"
                )
            verified = verify_artifact_payload(
                payload,
                source_mode=source_mode,
                plan_sha256=plan_sha,
            )
            if verified.logical_key != requirement.requirement_key:
                raise ValueError("Asset adapter returned wrong logical_key")
            _persist_verified_artifact(job_id, "ASSETS", verified)
            artifacts.append(verified)
            if source_mode == "GENERATED":
                record_provider_resource_usage(
                    job_id,
                    stage_key="ASSETS",
                    resource_type="NETWORK_BYTES",
                    quantity=verified.byte_size,
                    unit="bytes",
                    estimated_cost_usd=0,
                    metadata={
                        "adapter": verified.provenance.adapter_key,
                        "logical_key": verified.logical_key,
                    },
                    actor=actor,
                )

        manifest = AssetArtifactManifest(
            episode_id=brief.episode_id,
            asset_plan_sha256=plan_sha,
            artifacts=sorted(artifacts, key=lambda item: item.logical_key),
            reused_count=sum(item.source_mode == "REUSED" for item in artifacts),
            generated_count=sum(
                item.source_mode == "GENERATED" for item in artifacts
            ),
        )
        written = write_provider_manifest(
            job_id,
            "ASSETS",
            ProviderManifestEnvelope(
                manifest_kind="asset_artifact_manifest",
                schema_version=manifest.schema_version,
                payload=manifest.model_dump(mode="json"),
            ),
            actor=actor,
        )
        complete_provider_stage(job_id, "ASSETS", actor=actor)
        with engine.begin() as db:
            db.execute(
                text("""
                  UPDATE shrimp_animation_jobs
                  SET assets_manifest_sha256=:sha,updated_at=now()
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                """),
                {"job_id": job_id, "sha": written["content_sha256"]},
            )
        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "manifest_sha256": written["content_sha256"],
            "verified_artifacts": len(artifacts),
            "reused_count": manifest.reused_count,
            "generated_count": manifest.generated_count,
            "replayed": False,
        }
    except Exception as exc:
        fail_provider_stage(
            job_id,
            "ASSETS",
            str(exc),
            retryable=_retryable(exc),
            actor=actor,
        )
        raise


def _profile_metadata(profile_id: str) -> dict:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT voice_profile_id,adapter_hint,language,source_type,
                     provenance,usage_rights,metadata
              FROM animation_voice_profiles
              WHERE voice_profile_id=:id AND active=true
            """),
            {"id": profile_id},
        ).mappings().one_or_none()
    if row is None:
        raise LookupError(f"Voice profile not found: {profile_id}")
    result = dict(row)
    metadata = result.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    result["metadata"] = metadata
    return result


def execute_voice_stage(
    job_id,
    *,
    adapter: VoiceExecutionAdapter,
    actor: str = "shrimp-voices-worker",
) -> dict:
    content, plan_sha = _current_resource_plan(job_id, "VOICE")
    plan = VoicePlan.model_validate(content)
    if not plan.ready_for_synthesis:
        raise PermissionError("VOICE resource plan is not synthesis-ready")

    character_ids = sorted({item.speaker for item in plan.requests})
    profile_ids = sorted({item.voice_profile_id for item in plan.requests})
    snapshot = load_voice_registry_snapshot(
        character_ids=character_ids,
        requested_profile_ids=profile_ids,
    )
    if snapshot["snapshot_sha256"] != plan.registry_snapshot_sha256:
        raise RuntimeError("VOICE registry changed; rerun resource planning")

    job = get_provider_job(job_id)
    if _stage_state(job, "VOICES") == "SUCCEEDED":
        existing = _existing_stage_manifest(
            job_id,
            "VOICES",
            "voice_artifact_manifest",
        )
        if existing is None:
            raise RuntimeError("VOICES succeeded without artifact manifest")
        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "manifest_sha256": existing["content_sha256"],
            "replayed": True,
        }

    brief = _current_brief(job_id)
    _prepare_stage(job_id, "VOICES", actor=actor)
    artifacts: list[VerifiedArtifact] = []
    try:
        for request in plan.requests:
            cached = _load_current_verified(
                job_id,
                "VOICES",
                request.voice_asset_id,
                plan_sha,
            )
            if cached is not None:
                artifacts.append(cached)
                continue
            if request.status != "READY_FOR_SYNTHESIS":
                raise PermissionError(
                    f"Voice request is not synthesis-ready: {request.status}"
                )
            if (
                request.source_type == "CLONED_WITH_CONSENT"
                and not (request.provenance or "").strip()
            ):
                raise PermissionError(
                    "Cloned voice requires explicit consent/provenance"
                )
            profile = _profile_metadata(request.voice_profile_id)
            payload = adapter.synthesize(
                request,
                profile_metadata=profile["metadata"],
            )
            verified = verify_artifact_payload(
                payload,
                source_mode="GENERATED",
                plan_sha256=plan_sha,
            )
            if verified.logical_key != request.voice_asset_id:
                raise ValueError("Voice adapter returned wrong logical_key")
            _persist_verified_artifact(job_id, "VOICES", verified)
            artifacts.append(verified)
            record_provider_resource_usage(
                job_id,
                stage_key="VOICES",
                resource_type="NETWORK_BYTES",
                quantity=verified.byte_size,
                unit="bytes",
                estimated_cost_usd=0,
                metadata={
                    "adapter": verified.provenance.adapter_key,
                    "logical_key": verified.logical_key,
                },
                actor=actor,
            )

        manifest = VoiceArtifactManifest(
            episode_id=brief.episode_id,
            voice_plan_sha256=plan_sha,
            artifacts=sorted(artifacts, key=lambda item: item.logical_key),
            total_duration_ms=sum(
                int(item.duration_ms or 0) for item in artifacts
            ),
        )
        written = write_provider_manifest(
            job_id,
            "VOICES",
            ProviderManifestEnvelope(
                manifest_kind="voice_artifact_manifest",
                schema_version=manifest.schema_version,
                payload=manifest.model_dump(mode="json"),
            ),
            actor=actor,
        )
        complete_provider_stage(job_id, "VOICES", actor=actor)
        with engine.begin() as db:
            db.execute(
                text("""
                  UPDATE shrimp_animation_jobs
                  SET voices_manifest_sha256=:sha,updated_at=now()
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                """),
                {"job_id": job_id, "sha": written["content_sha256"]},
            )
        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "manifest_sha256": written["content_sha256"],
            "verified_artifacts": len(artifacts),
            "total_duration_ms": manifest.total_duration_ms,
            "replayed": False,
        }
    except Exception as exc:
        fail_provider_stage(
            job_id,
            "VOICES",
            str(exc),
            retryable=_retryable(exc),
            actor=actor,
        )
        raise


def list_shrimp_artifacts(
    job_id,
    *,
    include_superseded: bool = False,
) -> list[dict]:
    sql = """
      SELECT id,stage_key,logical_key,artifact_kind,source_mode,
             plan_sha256,storage_uri,media_type,byte_size,sha256,
             license_id,usage_rights,adapter_key,adapter_version,
             provider_request_id,provenance,duration_ms,
             verification_status,is_current,created_at,superseded_at
      FROM shrimp_animation_artifacts
      WHERE provider_job_id=CAST(:job_id AS uuid)
    """
    if not include_superseded:
        sql += " AND is_current=true"
    sql += " ORDER BY stage_key,logical_key,created_at DESC"
    with engine.connect() as db:
        return [
            dict(row)
            for row in db.execute(
                text(sql),
                {"job_id": job_id},
            ).mappings().all()
        ]
