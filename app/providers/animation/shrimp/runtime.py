from __future__ import annotations

from app.config import settings
from app.providers.animation.artifact_verification import LocalFileArtifactResolver
from app.providers.animation.shrimp.adapters.comfyui import ComfyUIAssetAdapter
from app.providers.animation.shrimp.adapters.gptsovits import GPTSoVITSAdapter


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
