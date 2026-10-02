from __future__ import annotations

from app.config import settings
from app.providers.animation.artifact_verification import LocalFileArtifactResolver
from app.providers.animation.shrimp.adapters.comfyui import ComfyUIAssetAdapter
from app.providers.animation.shrimp.adapters.gptsovits import GPTSoVITSAdapter
from app.providers.animation.shrimp.adapters.remotion import RemotionRendererAdapter


def _csv(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def build_artifact_resolver() -> LocalFileArtifactResolver:
    return LocalFileArtifactResolver(
        _csv(settings.shrimp_artifact_allowed_roots)
    )


def build_asset_adapter() -> ComfyUIAssetAdapter:
    if settings.shrimp_asset_adapter.upper() != "COMFYUI":
        raise RuntimeError(
            "Shrimp asset adapter is disabled; set SHRIMP_ASSET_ADAPTER=COMFYUI "
            "on an internal worker"
        )
    return ComfyUIAssetAdapter(
        base_url=settings.shrimp_comfyui_base_url,
        workflow_path=settings.shrimp_comfyui_workflow_path,
        generated_license_id=settings.shrimp_generated_asset_license_id,
        generated_provenance=settings.shrimp_generated_asset_provenance,
        allowed_hosts=_csv(settings.shrimp_internal_adapter_allowed_hosts),
        timeout_seconds=settings.shrimp_adapter_timeout_seconds,
    )


def build_voice_adapter() -> GPTSoVITSAdapter:
    if settings.shrimp_voice_adapter.upper() != "GPT_SOVITS":
        raise RuntimeError(
            "Shrimp voice adapter is disabled; set SHRIMP_VOICE_ADAPTER=GPT_SOVITS "
            "on an internal worker"
        )
    return GPTSoVITSAdapter(
        base_url=settings.shrimp_gptsovits_base_url,
        generated_license_id=settings.shrimp_generated_voice_license_id,
        generated_provenance=settings.shrimp_generated_voice_provenance,
        allowed_hosts=_csv(settings.shrimp_internal_adapter_allowed_hosts),
        timeout_seconds=settings.shrimp_adapter_timeout_seconds,
        tts_path=settings.shrimp_gptsovits_tts_path,
    )


def build_animation_adapter() -> RemotionRendererAdapter:
    if settings.shrimp_animation_adapter.upper() != "REMOTION":
        raise RuntimeError(
            "Shrimp animation adapter is disabled; set "
            "SHRIMP_ANIMATION_ADAPTER=REMOTION on an internal worker"
        )
    return RemotionRendererAdapter(
        project_dir=settings.shrimp_remotion_project_dir,
        props_output_root=settings.shrimp_remotion_props_output_root,
        entrypoint=settings.shrimp_remotion_entrypoint,
        composition_id=settings.shrimp_remotion_composition_id,
        remotion_cli=settings.shrimp_remotion_cli,
    )


def build_render_adapter() -> ControlledRemotionRenderAdapter:
    if settings.shrimp_render_adapter.upper() != "REMOTION":
        raise RuntimeError(
            "Shrimp render adapter is disabled; set "
            "SHRIMP_RENDER_ADAPTER=REMOTION on an internal worker"
        )
    return ControlledRemotionRenderAdapter(
        project_dir=settings.shrimp_remotion_project_dir,
        props_allowed_root=settings.shrimp_remotion_props_output_root,
        output_root=settings.shrimp_remotion_render_output_root,
        entrypoint=settings.shrimp_remotion_entrypoint,
        remotion_cli=settings.shrimp_remotion_cli,
        timeout_seconds=settings.shrimp_remotion_render_timeout_seconds,
    )


def run_render_stage(
    job_id,
    *,
    actor: str = "shrimp-render-worker",
) -> dict:
    return execute_render_stage(
        job_id,
        adapter=build_render_adapter(),
        ffprobe_cli=settings.shrimp_ffprobe_cli,
        actor=actor,
    )
