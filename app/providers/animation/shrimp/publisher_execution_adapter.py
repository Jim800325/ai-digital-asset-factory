from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class UploadReceipt:
    provider_upload_id: str
    state: str
    provider_write_performed: bool
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PublishReceipt:
    provider_publish_id: str
    url: str | None
    state: str
    provider_write_performed: bool
    metadata: dict[str, Any]


class PublisherWriteOutcomeUnknown(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        phase: str,
        error_type: str,
        evidence_sha256: str,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.error_type = error_type
        self.evidence_sha256 = evidence_sha256


class PublisherWriteRejected(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        phase: str,
        evidence_sha256: str,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.evidence_sha256 = evidence_sha256


class PublisherExecutionAdapter(Protocol):
    kind: str
    platform: str | None

    def validate_target(self, execution: dict[str, Any]) -> None:
        ...

    def upload(
        self,
        execution: dict[str, Any],
        *,
        media_path: Path,
        idempotency_key: str,
    ) -> UploadReceipt:
        ...

    def reconcile_upload(
        self,
        execution: dict[str, Any],
        *,
        idempotency_key: str,
        provider_upload_id: str | None,
    ) -> UploadReceipt | None:
        ...

    def publish(
        self,
        execution: dict[str, Any],
        *,
        provider_upload_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        ...

    def reconcile_publish(
        self,
        execution: dict[str, Any],
        *,
        idempotency_key: str,
        provider_publish_id: str | None,
    ) -> PublishReceipt | None:
        ...


class MockPublisherExecutionAdapter:
    kind = "MOCK"
    platform = None

    def validate_target(self, execution: dict[str, Any]) -> None:
        if execution.get("platform") not in {"BILIBILI", "YOUTUBE", "CUSTOM"}:
            raise RuntimeError("MOCK publisher received unsupported platform")

    def upload(
        self,
        execution: dict[str, Any],
        *,
        media_path: Path,
        idempotency_key: str,
    ) -> UploadReceipt:
        if not media_path.is_file():
            raise RuntimeError("Publisher media file is unavailable")
        token = hashlib.sha256(
            ("mock-upload|" + idempotency_key).encode("utf-8")
        ).hexdigest()[:24]
        return UploadReceipt(
            provider_upload_id="mock_up_" + token,
            state="UPLOADED",
            provider_write_performed=False,
            metadata={
                "adapter": self.kind,
                "external_side_effects": "DENY",
                "network_request_count": 0,
            },
        )

    def reconcile_upload(
        self,
        execution: dict[str, Any],
        *,
        idempotency_key: str,
        provider_upload_id: str | None,
    ) -> UploadReceipt | None:
        receipt = self.upload(
            execution,
            media_path=Path(__file__),
            idempotency_key=idempotency_key,
        )
        if provider_upload_id and provider_upload_id != receipt.provider_upload_id:
            return None
        return receipt

    def publish(
        self,
        execution: dict[str, Any],
        *,
        provider_upload_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        if not provider_upload_id:
            raise RuntimeError("Publisher publish requires provider upload ID")
        token = hashlib.sha256(
            ("mock-publish|" + idempotency_key).encode("utf-8")
        ).hexdigest()[:24]
        return PublishReceipt(
            provider_publish_id="mock_pub_" + token,
            url=f"https://{execution['platform'].lower()}.mock.invalid/{token}",
            state="PUBLISHED",
            provider_write_performed=False,
            metadata={
                "adapter": self.kind,
                "external_side_effects": "DENY",
                "network_request_count": 0,
            },
        )

    def reconcile_publish(
        self,
        execution: dict[str, Any],
        *,
        idempotency_key: str,
        provider_publish_id: str | None,
    ) -> PublishReceipt | None:
        receipt = self.publish(
            execution,
            provider_upload_id=(
                execution.get("provider_upload_id") or "mock-reconciled-upload"
            ),
            idempotency_key=idempotency_key,
        )
        if (
            provider_publish_id
            and provider_publish_id != receipt.provider_publish_id
        ):
            return None
        return receipt


class _UnconfiguredLivePublisherAdapter:
    platform: str
    kind: str

    def validate_target(self, execution: dict[str, Any]) -> None:
        if execution.get("platform") != self.platform:
            raise RuntimeError(
                f"{self.kind} requires platform={self.platform}"
            )

    def _unavailable(self) -> RuntimeError:
        return RuntimeError(
            f"{self.kind} provider implementation is not installed; "
            "Step 10 defines the controlled adapter contract but does not "
            "ship real platform credentials or an automatic live publisher"
        )

    def upload(
        self,
        execution: dict[str, Any],
        *,
        media_path: Path,
        idempotency_key: str,
    ) -> UploadReceipt:
        raise self._unavailable()

    def reconcile_upload(
        self,
        execution: dict[str, Any],
        *,
        idempotency_key: str,
        provider_upload_id: str | None,
    ) -> UploadReceipt | None:
        raise self._unavailable()

    def publish(
        self,
        execution: dict[str, Any],
        *,
        provider_upload_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        raise self._unavailable()

    def reconcile_publish(
        self,
        execution: dict[str, Any],
        *,
        idempotency_key: str,
        provider_publish_id: str | None,
    ) -> PublishReceipt | None:
        raise self._unavailable()


class BilibiliControlledPublisherAdapter(_UnconfiguredLivePublisherAdapter):
    kind = "BILIBILI_CONTROLLED"
    platform = "BILIBILI"


class YouTubeControlledPublisherAdapter(_UnconfiguredLivePublisherAdapter):
    kind = "YOUTUBE_CONTROLLED"
    platform = "YOUTUBE"


def get_publisher_execution_adapter(kind: str) -> PublisherExecutionAdapter:
    normalized = (kind or "").upper().strip() or "MOCK"
    if normalized == "MOCK":
        return MockPublisherExecutionAdapter()
    if normalized == "BILIBILI_CONTROLLED":
        return BilibiliControlledPublisherAdapter()
    if normalized == "YOUTUBE_CONTROLLED":
        from app.providers.animation.shrimp.youtube_live_publisher import (
            YouTubeLivePublisherAdapter,
        )
        return YouTubeLivePublisherAdapter()
    raise RuntimeError("Unsupported Shrimp publisher execution adapter")
