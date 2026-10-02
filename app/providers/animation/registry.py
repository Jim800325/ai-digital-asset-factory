from app.providers.animation.asset_registry import ensure_builtin_animation_primitives
from app.production_provider_contract import (
    ProviderStageSpec,
    ProductionProviderSpec,
    register_provider_definition,
)


SHRIMP_ANIMATION_SPEC = ProductionProviderSpec(
    provider_key="shrimp_animation",
    asset_class="CONTENT_IP",
    provider_version="v0.1",
    contract_version="v0.1",
    execution_mode="SANDBOX_FIRST",
    external_publish_mode="HUMAN_GATED",
    capabilities=(
        "deterministic_story_planning",
        "deterministic_script_planning",
        "deterministic_scene_planning",
        "reusable_assets",
        "reusable_asset_registry",
        "deterministic_asset_planning",
        "voice_profile_rights_registry",
        "deterministic_voice_planning",
        "comfyui_asset_adapter",
        "gpt_sovits_adapter",
        "artifact_hash_verification",
        "artifact_provenance_verification",
        "deterministic_animation_timeline",
        "dialogue_subtitle_timeline",
        "camera_timeline_composition",
        "remotion_composition_adapter",
        "controlled_remotion_render_execution",
        "mp4_media_verification",
        "render_artifact_provenance",
        "tts",
        "remotion_render",
        "ffmpeg_package",
        "qc",
    ),
    stages=(
        ProviderStageSpec("CONTENT_BRIEF", "PLAN", (), 2),
        ProviderStageSpec("STORY", "GENERATE", ("CONTENT_BRIEF",), 3),
        ProviderStageSpec("SCRIPT", "GENERATE", ("STORY",), 3),
        ProviderStageSpec("SCENE", "GENERATE", ("SCRIPT",), 3),
        ProviderStageSpec("ASSETS", "GENERATE", ("SCENE",), 3),
        ProviderStageSpec("VOICES", "GENERATE", ("SCRIPT",), 3),
        ProviderStageSpec(
            "ANIMATION",
            "GENERATE",
            ("SCENE", "ASSETS", "VOICES"),
            3,
        ),
        ProviderStageSpec("RENDER", "PACKAGE", ("ANIMATION",), 2),
        ProviderStageSpec("QC", "QC", ("RENDER",), 2),
    ),
)


def register_shrimp_animation_provider() -> dict:
    result = register_provider_definition(SHRIMP_ANIMATION_SPEC)
    ensure_builtin_animation_primitives()
    return result
