from __future__ import annotations

import hashlib
import json
from app.providers.animation.artifact_verification import AdapterArtifactPayload
from app.providers.animation.models import (
    ArtifactProvenance,
    VoiceRequest,
    canonical_json,
)
from app.providers.animation.shrimp.adapters.internal_http import (
    post_json,
    validate_internal_base_url,
)


class GPTSoVITSAdapter:
    adapter_key = "GPT_SOVITS"
    adapter_version = "v0.1"

    def __init__(
        self,
        *,
        base_url: str,
        generated_license_id: str,
        generated_provenance: str,
        allowed_hosts: list[str] | tuple[str, ...] = (),
        timeout_seconds: float = 120.0,
        tts_path: str = "/tts",
    ):
        self.base_url = validate_internal_base_url(
            base_url,
            allowed_hosts=allowed_hosts,
        )
        self.generated_license_id = generated_license_id.strip()
        self.generated_provenance = generated_provenance.strip()
        if not self.generated_license_id:
            raise ValueError("generated_license_id is required")
        if not self.generated_provenance:
            raise ValueError("generated_provenance is required")
        self.timeout_seconds = float(timeout_seconds)
        self.tts_path = "/" + tts_path.strip().lstrip("/")

    def synthesize(
        self,
        request: VoiceRequest,
        *,
        profile_metadata: dict,
    ) -> AdapterArtifactPayload:
        ref_audio_path = str(
            profile_metadata.get("ref_audio_path") or ""
        ).strip()
        prompt_text = str(
            profile_metadata.get("prompt_text") or ""
        )
        prompt_lang = str(
            profile_metadata.get("prompt_lang")
            or request.language
        )
        if not ref_audio_path:
            raise ValueError(
                "GPT-SoVITS voice profile requires ref_audio_path metadata"
            )

        payload = {
            "text": request.text,
            "text_lang": request.language,
            "ref_audio_path": ref_audio_path,
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang,
            "media_type": "wav",
            "streaming_mode": False,
        }
        request_sha = hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()
        content, headers = post_json(
            f"{self.base_url}{self.tts_path}",
            payload,
            timeout_seconds=self.timeout_seconds,
        )
        provider_request_id = (
            headers.get("x-request-id")
            or headers.get("x-trace-id")
            or request_sha[:24]
        )
        storage_uri = (
            f"generated://gpt-sovits/{request.voice_asset_id}.wav"
        )
        return AdapterArtifactPayload(
            logical_key=request.voice_asset_id,
            artifact_kind="VOICE",
            content=content,
            storage_uri=storage_uri,
            media_type="audio/wav",
            license_id=self.generated_license_id,
            usage_rights="APPROVED",
            provenance=ArtifactProvenance(
                adapter_key=self.adapter_key,
                adapter_version=self.adapter_version,
                provider_request_id=provider_request_id,
                source_reference=ref_audio_path,
                source_type="TTS_GENERATED",
                provenance=(
                    f"{self.generated_provenance}; "
                    f"voice_profile_id={request.voice_profile_id}; "
                    f"source_type={request.source_type}"
                ),
                request_sha256=request_sha,
            ),
        )
