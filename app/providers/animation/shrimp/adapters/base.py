from __future__ import annotations

from typing import Protocol

from app.providers.animation.artifact_verification import AdapterArtifactPayload
from app.providers.animation.models import AssetRequirement, VoiceRequest


class AssetExecutionAdapter(Protocol):
    adapter_key: str
    adapter_version: str

    def generate(
        self,
        requirement: AssetRequirement,
        *,
        prompt: str,
        seed: int,
        output_prefix: str,
    ) -> AdapterArtifactPayload: ...


class VoiceExecutionAdapter(Protocol):
    adapter_key: str
    adapter_version: str

    def synthesize(
        self,
        request: VoiceRequest,
        *,
        profile_metadata: dict,
    ) -> AdapterArtifactPayload: ...
