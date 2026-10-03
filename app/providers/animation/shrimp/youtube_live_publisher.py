from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.providers.animation.shrimp.publisher_execution_adapter import (
    PublishReceipt,
    PublisherWriteOutcomeUnknown,
    PublisherWriteRejected,
    UploadReceipt,
    sha256_json,
)


class YouTubeLivePublisherAdapter:
    """Controlled YouTube Data API adapter for Step 10A.

    Safety invariants:
    - OAuth-authenticated channel must exactly match execution.account_reference.
    - Upload and metadata-finalization are always private.
    - No automatic write retries.
    - Unknown write outcomes are surfaced to Step 10 reconciliation.
    - Reconciliation uses read-only API calls and a deterministic marker.
    """

    kind = "YOUTUBE_CONTROLLED"
    platform = "YOUTUBE"

    def __init__(self) -> None:
        self._access_token_value: str | None = None
        self._access_token_expires_at = 0.0

    @staticmethod
    def _marker(idempotency_key: str) -> str:
        return f"[shrimp-step10a:{idempotency_key}]"

    @staticmethod
    def _safe_title(value: Any, fallback: str) -> str:
        title = str(value or fallback).replace("<", "").replace(">", "").strip()
        if not title:
            title = fallback
        return title[:100]

    @staticmethod
    def _description_with_marker(value: Any, marker: str) -> str:
        base = str(value or "").replace("<", "").replace(">", "").strip()
        suffix = ("\n\n" if base else "") + marker
        suffix_bytes = suffix.encode("utf-8")
        if len(suffix_bytes) >= 5000:
            raise RuntimeError("YouTube reconciliation marker is unexpectedly too large")
        budget = 5000 - len(suffix_bytes)
        raw = base.encode("utf-8")[:budget]
        while True:
            try:
                trimmed = raw.decode("utf-8")
                break
            except UnicodeDecodeError:
                raw = raw[:-1]
        return trimmed.rstrip() + suffix

    @staticmethod
    def _clean_tags(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value[:20]:
            tag = str(item).strip().replace("<", "").replace(">", "")
            if tag:
                result.append(tag[:80])
        return result

    def _credentials_present(self) -> bool:
        return all(
            (
                settings.shrimp_youtube_oauth_client_id.strip(),
                settings.shrimp_youtube_oauth_client_secret.strip(),
                settings.shrimp_youtube_oauth_refresh_token.strip(),
            )
        )

    def validate_target(self, execution: dict[str, Any]) -> None:
        if execution.get("platform") != "YOUTUBE":
            raise RuntimeError("YOUTUBE_CONTROLLED requires platform=YOUTUBE")
        if not settings.shrimp_youtube_live_acceptance_enabled:
            raise RuntimeError("YouTube Step 10A live acceptance is disabled")
        if not self._credentials_present():
            raise RuntimeError("YouTube Step 10A OAuth credentials are incomplete")
        expected = str(execution.get("account_reference") or "").strip()
        if not expected:
            raise RuntimeError("YouTube Step 10A requires exact channel ID")
        if not expected.startswith("UC"):
            raise RuntimeError(
                "YouTube account_reference must be the exact UC... channel ID"
            )

    def _token(self) -> str:
        now = time.monotonic()
        if self._access_token_value and now < self._access_token_expires_at:
            return self._access_token_value

        try:
            response = httpx.post(
                settings.shrimp_youtube_token_url,
                data={
                    "client_id": settings.shrimp_youtube_oauth_client_id,
                    "client_secret": settings.shrimp_youtube_oauth_client_secret,
                    "refresh_token": settings.shrimp_youtube_oauth_refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=settings.shrimp_youtube_request_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"YouTube OAuth refresh failed: {type(exc).__name__}"
            ) from exc

        if response.status_code >= 400:
            raise RuntimeError(
                "YouTube OAuth refresh rejected "
                f"with HTTP {response.status_code}"
            )
        payload = response.json()
        token = str(payload.get("access_token") or "")
        if not token:
            raise RuntimeError("YouTube OAuth response did not contain access_token")
        expires_in = int(payload.get("expires_in") or 3600)
        self._access_token_value = token
        self._access_token_expires_at = now + max(60, expires_in - 60)
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
        }

    @staticmethod
    def _error_evidence(
        *,
        phase: str,
        method: str,
        status_code: int | None,
        body: str | None,
        error_type: str,
    ) -> str:
        return sha256_json(
            {
                "phase": phase,
                "method": method,
                "status_code": status_code,
                "body_sha256": (
                    hashlib.sha256((body or "").encode("utf-8")).hexdigest()
                    if body is not None
                    else None
                ),
                "error_type": error_type,
            }
        )

    def _read(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = httpx.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                timeout=settings.shrimp_youtube_request_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"YouTube read failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise RuntimeError(
                f"YouTube read rejected with HTTP {response.status_code}"
            )
        return response

    def _write_json(
        self,
        *,
        phase: str,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        payload: dict[str, Any],
    ) -> httpx.Response:
        try:
            response = httpx.request(
                method,
                url,
                headers={
                    **self._headers(),
                    "Content-Type": "application/json; charset=UTF-8",
                },
                params=params,
                json=payload,
                timeout=settings.shrimp_youtube_request_timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            evidence = self._error_evidence(
                phase=phase,
                method=method,
                status_code=None,
                body=None,
                error_type=type(exc).__name__,
            )
            raise PublisherWriteOutcomeUnknown(
                f"YouTube {phase} outcome is unknown",
                phase=phase,
                error_type=type(exc).__name__,
                evidence_sha256=evidence,
            ) from exc

        if response.status_code >= 500:
            evidence = self._error_evidence(
                phase=phase,
                method=method,
                status_code=response.status_code,
                body=response.text,
                error_type="YouTube5xx",
            )
            raise PublisherWriteOutcomeUnknown(
                f"YouTube {phase} returned HTTP {response.status_code}",
                phase=phase,
                error_type="YouTube5xx",
                evidence_sha256=evidence,
            )

        if response.status_code >= 400:
            evidence = self._error_evidence(
                phase=phase,
                method=method,
                status_code=response.status_code,
                body=response.text,
                error_type="YouTubeRejected",
            )
            raise PublisherWriteRejected(
                f"YouTube {phase} rejected with HTTP {response.status_code}",
                phase=phase,
                evidence_sha256=evidence,
            )
        return response

    def _channel(self) -> dict[str, Any]:
        response = self._read(
            "GET",
            f"{settings.shrimp_youtube_api_base.rstrip('/')}/channels",
            params={"part": "id,snippet,contentDetails", "mine": "true"},
        )
        items = response.json().get("items") or []
        if len(items) != 1:
            raise RuntimeError(
                "YouTube OAuth identity must resolve to exactly one channel"
            )
        return items[0]

    def _category_id(self, execution: dict[str, Any]) -> str:
        metadata = dict(execution.get("publish_metadata") or {})
        value = str(
            metadata.get("youtube_category_id")
            or settings.shrimp_youtube_default_category_id
        ).strip()
        if not value:
            raise RuntimeError("YouTube category ID is required")
        return value

    def preflight(self, execution: dict[str, Any]) -> dict[str, Any]:
        self.validate_target(execution)
        channel = self._channel()
        actual = str(channel.get("id") or "")
        expected = str(execution["account_reference"])
        if actual != expected:
            raise RuntimeError(
                "YouTube authenticated channel does not match sacrificial account"
            )

        category_id = self._category_id(execution)
        category_response = self._read(
            "GET",
            f"{settings.shrimp_youtube_api_base.rstrip('/')}/videoCategories",
            params={"part": "snippet", "id": category_id},
        )
        categories = category_response.json().get("items") or []
        if len(categories) != 1:
            raise RuntimeError("YouTube category ID is unavailable")
        if not bool((categories[0].get("snippet") or {}).get("assignable")):
            raise RuntimeError("YouTube category ID is not assignable")

        uploads_playlist = (
            ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {})
            .get("uploads")
        )
        if not uploads_playlist:
            raise RuntimeError("YouTube channel uploads playlist is unavailable")
        return {
            "channel_id": actual,
            "channel_title": (channel.get("snippet") or {}).get("title"),
            "uploads_playlist_id": uploads_playlist,
            "category_id": category_id,
        }

    def _video(self, video_id: str) -> dict[str, Any] | None:
        response = self._read(
            "GET",
            f"{settings.shrimp_youtube_api_base.rstrip('/')}/videos",
            params={
                "part": "snippet,status,processingDetails",
                "id": video_id,
            },
        )
        items = response.json().get("items") or []
        if not items:
            return None
        return items[0]

    def _owned_video(
        self,
        execution: dict[str, Any],
        *,
        video_id: str,
        marker: str | None = None,
    ) -> dict[str, Any] | None:
        self.validate_target(execution)
        item = self._video(video_id)
        if item is None:
            return None
        snippet = item.get("snippet") or {}
        if str(snippet.get("channelId") or "") != str(
            execution["account_reference"]
        ):
            raise RuntimeError("YouTube provider object channel binding mismatch")
        if marker and marker not in str(snippet.get("description") or ""):
            raise RuntimeError("YouTube reconciliation marker is missing")
        return item

    def read_back_video(
        self,
        execution: dict[str, Any],
        *,
        video_id: str,
        marker: str | None = None,
    ) -> dict[str, Any]:
        item = self._owned_video(
            execution,
            video_id=video_id,
            marker=marker,
        )
        if item is None:
            raise RuntimeError("YouTube video is not present")
        snippet = item.get("snippet") or {}
        status = item.get("status") or {}
        processing = item.get("processingDetails") or {}
        privacy = str(status.get("privacyStatus") or "")
        if privacy != "private":
            raise RuntimeError(
                f"YouTube Step 10A requires private visibility, got {privacy!r}"
            )
        return {
            "video_id": video_id,
            "channel_id": snippet.get("channelId"),
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "category_id": snippet.get("categoryId"),
            "privacy_status": privacy,
            "processing_status": processing.get("processingStatus"),
            "upload_status": status.get("uploadStatus"),
        }

    def upload(
        self,
        execution: dict[str, Any],
        *,
        media_path: Path,
        idempotency_key: str,
    ) -> UploadReceipt:
        preflight = self.preflight(execution)
        if not media_path.is_file():
            raise RuntimeError("YouTube upload media file is unavailable")
        size = media_path.stat().st_size
        if size <= 0:
            raise RuntimeError("YouTube upload media file is empty")
        if size > settings.shrimp_youtube_live_acceptance_max_media_bytes:
            raise RuntimeError(
                "YouTube Step 10A media exceeds sacrificial acceptance size limit"
            )

        marker = self._marker(idempotency_key)
        metadata = {
            "snippet": {
                "title": self._safe_title(
                    f"Shrimp Step10A {idempotency_key[:12]}",
                    "Shrimp Step10A",
                ),
                "description": self._description_with_marker(
                    "Controlled sacrificial live-publisher acceptance. "
                    "This private video is scheduled for automatic cleanup.",
                    marker,
                ),
                "categoryId": preflight["category_id"],
            },
            "status": {
                "privacyStatus": "private",
            },
        }

        boundary = f"shrimp_step10a_{idempotency_key[:24]}"
        media = media_path.read_bytes()
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        ).encode("utf-8")
        body += json.dumps(
            metadata,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        body += (
            f"\r\n--{boundary}\r\n"
            "Content-Type: video/mp4\r\n"
            "Content-Transfer-Encoding: binary\r\n\r\n"
        ).encode("utf-8")
        body += media
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        url = f"{settings.shrimp_youtube_upload_base.rstrip('/')}/videos"
        try:
            response = httpx.post(
                url,
                headers={
                    **self._headers(),
                    "Content-Type": f"multipart/related; boundary={boundary}",
                    "Content-Length": str(len(body)),
                },
                params={
                    "uploadType": "multipart",
                    "part": "snippet,status",
                },
                content=body,
                timeout=settings.shrimp_youtube_request_timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            evidence = self._error_evidence(
                phase="UPLOAD",
                method="POST",
                status_code=None,
                body=None,
                error_type=type(exc).__name__,
            )
            raise PublisherWriteOutcomeUnknown(
                "YouTube upload outcome is unknown",
                phase="UPLOAD",
                error_type=type(exc).__name__,
                evidence_sha256=evidence,
            ) from exc

        if response.status_code >= 500:
            evidence = self._error_evidence(
                phase="UPLOAD",
                method="POST",
                status_code=response.status_code,
                body=response.text,
                error_type="YouTube5xx",
            )
            raise PublisherWriteOutcomeUnknown(
                f"YouTube upload returned HTTP {response.status_code}",
                phase="UPLOAD",
                error_type="YouTube5xx",
                evidence_sha256=evidence,
            )
        if response.status_code >= 400:
            evidence = self._error_evidence(
                phase="UPLOAD",
                method="POST",
                status_code=response.status_code,
                body=response.text,
                error_type="YouTubeRejected",
            )
            raise PublisherWriteRejected(
                f"YouTube upload rejected with HTTP {response.status_code}",
                phase="UPLOAD",
                evidence_sha256=evidence,
            )

        payload = response.json()
        video_id = str(payload.get("id") or "")
        if not video_id:
            raise PublisherWriteOutcomeUnknown(
                "YouTube upload succeeded without a video ID",
                phase="UPLOAD",
                error_type="MissingVideoId",
                evidence_sha256=sha256_json(
                    {
                        "phase": "UPLOAD",
                        "response_sha256": hashlib.sha256(
                            response.content
                        ).hexdigest(),
                    }
                ),
            )

        response_snippet = payload.get("snippet") or {}
        response_status = payload.get("status") or {}
        return UploadReceipt(
            provider_upload_id=video_id,
            state=str(response_status.get("uploadStatus") or "UPLOADED"),
            provider_write_performed=True,
            metadata={
                "adapter": self.kind,
                "channel_id": response_snippet.get("channelId"),
                "privacy_status": response_status.get("privacyStatus"),
                "processing_status": None,
                "reconciliation_marker": marker,
                "provider_response_sha256": hashlib.sha256(
                    response.content
                ).hexdigest(),
                "read_back_deferred": True,
            },
        )

    def _recent_videos(
        self,
        execution: dict[str, Any],
    ) -> list[dict[str, Any]]:
        preflight = self.preflight(execution)
        playlist_response = self._read(
            "GET",
            f"{settings.shrimp_youtube_api_base.rstrip('/')}/playlistItems",
            params={
                "part": "contentDetails",
                "playlistId": preflight["uploads_playlist_id"],
                "maxResults": min(
                    50,
                    max(1, settings.shrimp_youtube_reconcile_recent_limit),
                ),
            },
        )
        ids = [
            str((item.get("contentDetails") or {}).get("videoId") or "")
            for item in (playlist_response.json().get("items") or [])
        ]
        ids = [item for item in ids if item]
        if not ids:
            return []
        videos_response = self._read(
            "GET",
            f"{settings.shrimp_youtube_api_base.rstrip('/')}/videos",
            params={
                "part": "snippet,status,processingDetails",
                "id": ",".join(ids),
            },
        )
        return list(videos_response.json().get("items") or [])

    def reconcile_upload(
        self,
        execution: dict[str, Any],
        *,
        idempotency_key: str,
        provider_upload_id: str | None,
    ) -> UploadReceipt | None:
        marker = self._marker(idempotency_key)
        item: dict[str, Any] | None = None
        if provider_upload_id:
            item = self._video(provider_upload_id)
        else:
            matches = []
            for candidate in self._recent_videos(execution):
                snippet = candidate.get("snippet") or {}
                if marker in str(snippet.get("description") or ""):
                    matches.append(candidate)
            if len(matches) > 1:
                raise RuntimeError(
                    "YouTube reconciliation found multiple marker matches"
                )
            item = matches[0] if matches else None

        if item is None:
            return None
        video_id = str(item.get("id") or "")
        readback = self.read_back_video(
            execution,
            video_id=video_id,
            marker=marker,
        )
        return UploadReceipt(
            provider_upload_id=video_id,
            state=str(readback.get("upload_status") or "UPLOADED"),
            provider_write_performed=False,
            metadata={
                "adapter": self.kind,
                "reconciled": True,
                "privacy_status": readback["privacy_status"],
                "processing_status": readback["processing_status"],
                "reconciliation_marker": marker,
            },
        )

    def publish(
        self,
        execution: dict[str, Any],
        *,
        provider_upload_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        self.preflight(execution)
        upload_key = str(execution.get("upload_idempotency_key") or "")
        marker = self._marker(upload_key)
        existing = self.read_back_video(
            execution,
            video_id=provider_upload_id,
            marker=marker,
        )
        metadata = dict(execution.get("publish_metadata") or {})
        title = self._safe_title(
            metadata.get("title"),
            existing.get("title") or "Shrimp Step10A",
        )
        description = self._description_with_marker(
            metadata.get("description"),
            marker,
        )
        category_id = self._category_id(execution)
        body: dict[str, Any] = {
            "id": provider_upload_id,
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": "private",
            },
        }
        tags = self._clean_tags(metadata.get("tags"))
        if tags:
            body["snippet"]["tags"] = tags

        response = self._write_json(
            phase="PUBLISH",
            method="PUT",
            url=f"{settings.shrimp_youtube_api_base.rstrip('/')}/videos",
            params={"part": "snippet,status"},
            payload=body,
        )
        payload = response.json()
        response_video_id = str(payload.get("id") or provider_upload_id)
        if response_video_id != provider_upload_id:
            response_video_id = provider_upload_id
        response_snippet = payload.get("snippet") or {}
        response_status = payload.get("status") or {}
        return PublishReceipt(
            provider_publish_id=response_video_id,
            url=f"https://www.youtube.com/watch?v={response_video_id}",
            state="PRIVATE",
            provider_write_performed=True,
            metadata={
                "adapter": self.kind,
                "privacy_status": response_status.get("privacyStatus"),
                "processing_status": None,
                "final_title": response_snippet.get("title") or title,
                "reconciliation_marker": marker,
                "provider_response_sha256": hashlib.sha256(
                    response.content
                ).hexdigest(),
                "read_back_deferred": True,
            },
        )

    def reconcile_publish(
        self,
        execution: dict[str, Any],
        *,
        idempotency_key: str,
        provider_publish_id: str | None,
    ) -> PublishReceipt | None:
        video_id = (
            provider_publish_id
            or execution.get("provider_upload_id")
        )
        if not video_id:
            return None
        upload_key = str(execution.get("upload_idempotency_key") or "")
        marker = self._marker(upload_key)
        item = self._video(str(video_id))
        if item is None:
            return None
        readback = self.read_back_video(
            execution,
            video_id=str(video_id),
            marker=marker,
        )
        metadata = dict(execution.get("publish_metadata") or {})
        expected_title = self._safe_title(
            metadata.get("title"),
            readback.get("title") or "Shrimp Step10A",
        )
        if readback["title"] != expected_title:
            return None
        return PublishReceipt(
            provider_publish_id=str(video_id),
            url=f"https://www.youtube.com/watch?v={video_id}",
            state="PRIVATE",
            provider_write_performed=False,
            metadata={
                "adapter": self.kind,
                "reconciled": True,
                "privacy_status": readback["privacy_status"],
                "processing_status": readback["processing_status"],
                "reconciliation_marker": marker,
            },
        )

    def delete_video(
        self,
        execution: dict[str, Any],
        *,
        video_id: str,
    ) -> dict[str, Any]:
        self.preflight(execution)
        upload_key = str(execution.get("upload_idempotency_key") or "")
        marker = self._marker(upload_key)
        owned = self._owned_video(
            execution,
            video_id=video_id,
            marker=marker,
        )
        if owned is None:
            return {
                "video_id": video_id,
                "provider_write_performed": False,
                "already_deleted": True,
            }
        url = f"{settings.shrimp_youtube_api_base.rstrip('/')}/videos"
        try:
            response = httpx.delete(
                url,
                headers=self._headers(),
                params={"id": video_id},
                timeout=settings.shrimp_youtube_request_timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            evidence = self._error_evidence(
                phase="CLEANUP",
                method="DELETE",
                status_code=None,
                body=None,
                error_type=type(exc).__name__,
            )
            raise PublisherWriteOutcomeUnknown(
                "YouTube cleanup outcome is unknown",
                phase="CLEANUP",
                error_type=type(exc).__name__,
                evidence_sha256=evidence,
            ) from exc

        if response.status_code >= 500:
            evidence = self._error_evidence(
                phase="CLEANUP",
                method="DELETE",
                status_code=response.status_code,
                body=response.text,
                error_type="YouTube5xx",
            )
            raise PublisherWriteOutcomeUnknown(
                f"YouTube cleanup returned HTTP {response.status_code}",
                phase="CLEANUP",
                error_type="YouTube5xx",
                evidence_sha256=evidence,
            )
        if response.status_code not in (200, 204):
            evidence = self._error_evidence(
                phase="CLEANUP",
                method="DELETE",
                status_code=response.status_code,
                body=response.text,
                error_type="YouTubeRejected",
            )
            raise PublisherWriteRejected(
                f"YouTube cleanup rejected with HTTP {response.status_code}",
                phase="CLEANUP",
                evidence_sha256=evidence,
            )
        return {
            "video_id": video_id,
            "provider_write_performed": True,
            "http_status": response.status_code,
        }

    def verify_deleted(
        self,
        execution: dict[str, Any],
        *,
        video_id: str,
    ) -> bool:
        self.validate_target(execution)
        return self._video(video_id) is None
