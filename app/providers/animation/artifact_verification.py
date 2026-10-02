from __future__ import annotations

import hashlib
import io
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

from app.providers.animation.models import ArtifactProvenance, VerifiedArtifact


ASSET_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp"}
VOICE_MEDIA_TYPES = {"audio/wav", "audio/x-wav", "audio/wave"}


@dataclass(frozen=True)
class AdapterArtifactPayload:
    logical_key: str
    artifact_kind: str
    content: bytes
    storage_uri: str
    media_type: str
    license_id: str
    usage_rights: str
    provenance: ArtifactProvenance
    claimed_sha256: str | None = None
    duration_ms: int | None = None


class ArtifactByteResolver(Protocol):
    def read_bytes(self, storage_uri: str) -> bytes: ...


class LocalFileArtifactResolver:
    def __init__(self, allowed_roots: list[str] | tuple[str, ...]):
        roots = [Path(root).expanduser().resolve() for root in allowed_roots if root]
        if not roots:
            raise ValueError("At least one artifact storage root is required")
        self.allowed_roots = tuple(roots)

    def read_bytes(self, storage_uri: str) -> bytes:
        parsed = urlparse(storage_uri)
        if parsed.scheme not in {"", "file"}:
            raise ValueError("Local resolver accepts only file paths/file:// URIs")
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path))
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                path = Path(f"//{parsed.netloc}{unquote(parsed.path)}")
        else:
            path = Path(storage_uri)
        resolved = path.expanduser().resolve()
        if not any(
            resolved == root or root in resolved.parents
            for root in self.allowed_roots
        ):
            raise PermissionError("Artifact path is outside configured roots")
        data = resolved.read_bytes()
        if not data:
            raise ValueError("Artifact file is empty")
        return data


def sha256_bytes(content: bytes) -> str:
    if not content:
        raise ValueError("Artifact content is empty")
    return hashlib.sha256(content).hexdigest()


def wav_duration_ms(content: bytes) -> int:
    try:
        with wave.open(io.BytesIO(content), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            if frames <= 0 or rate <= 0:
                raise ValueError("WAV has no audio frames")
            duration = int(round(frames * 1000 / rate))
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"Invalid WAV artifact: {exc}") from exc
    if duration <= 0:
        raise ValueError("WAV duration must be positive")
    return duration


def verify_artifact_payload(
    payload: AdapterArtifactPayload,
    *,
    source_mode: str,
    plan_sha256: str,
) -> VerifiedArtifact:
    if source_mode not in {"REUSED", "GENERATED"}:
        raise ValueError("Unsupported artifact source_mode")
    if payload.usage_rights != "APPROVED":
        raise PermissionError("Artifact usage rights are not APPROVED")
    if not payload.license_id.strip():
        raise ValueError("Artifact license_id is required")
    if not payload.storage_uri.strip():
        raise ValueError("Artifact storage_uri is required")
    if not payload.provenance.adapter_key.strip():
        raise ValueError("Artifact adapter provenance is required")

    if payload.artifact_kind in {"CHARACTER", "BACKGROUND"}:
        if payload.media_type not in ASSET_MEDIA_TYPES:
            raise ValueError("Unsupported image artifact media_type")
        if payload.media_type == "image/png" and not payload.content.startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            raise ValueError("PNG artifact signature is invalid")
        if payload.media_type == "image/jpeg" and not payload.content.startswith(
            b"\xff\xd8\xff"
        ):
            raise ValueError("JPEG artifact signature is invalid")
        if payload.media_type == "image/webp" and not (
            len(payload.content) >= 12
            and payload.content[:4] == b"RIFF"
            and payload.content[8:12] == b"WEBP"
        ):
            raise ValueError("WebP artifact signature is invalid")
        duration_ms = None
    elif payload.artifact_kind == "VOICE":
        if payload.media_type not in VOICE_MEDIA_TYPES:
            raise ValueError("Unsupported voice artifact media_type")
        duration_ms = wav_duration_ms(payload.content)
        if payload.duration_ms is not None and abs(payload.duration_ms - duration_ms) > 50:
            raise ValueError("Claimed voice duration does not match WAV content")
    else:
        raise ValueError("Unsupported artifact_kind")

    computed_sha256 = sha256_bytes(payload.content)
    if (
        payload.claimed_sha256 is not None
        and payload.claimed_sha256 != computed_sha256
    ):
        raise ValueError("Artifact SHA-256 mismatch")

    return VerifiedArtifact(
        logical_key=payload.logical_key,
        artifact_kind=payload.artifact_kind,
        source_mode=source_mode,
        plan_sha256=plan_sha256,
        storage_uri=payload.storage_uri,
        media_type=payload.media_type,
        byte_size=len(payload.content),
        sha256=computed_sha256,
        license_id=payload.license_id,
        usage_rights="APPROVED",
        provenance=payload.provenance,
        duration_ms=duration_ms,
    )
