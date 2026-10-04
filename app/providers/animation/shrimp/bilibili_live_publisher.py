from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx

from app.config import settings
from app.providers.animation.shrimp.publisher_execution_adapter import (
    PublishReceipt,
    PublisherWriteOutcomeUnknown,
    PublisherWriteRejected,
    UploadReceipt,
    sha256_json,
)


class BilibiliLivePublisherAdapter:
    kind = "BILIBILI_CONTROLLED"
    platform = "BILIBILI"

    def __init__(self, credentials: dict[str, str] | None = None) -> None:
        creds = dict(credentials or {})
        self._csrf = (
            creds.get("bili_jct")
            or settings.shrimp_bilibili_bili_jct
        ).strip()
        self._sessdata = (
            creds.get("sessdata")
            or settings.shrimp_bilibili_sessdata
        ).strip()
        self._dede_user_id = (
            creds.get("dede_user_id")
            or settings.shrimp_bilibili_dede_user_id
        ).strip()
        self._dede_user_id_ckmd5 = (
            creds.get("dede_user_id_ckmd5")
            or settings.shrimp_bilibili_dede_user_id_ckmd5
        ).strip()
        if not self._csrf or not self._sessdata or not self._dede_user_id:
            raise RuntimeError(
                "Bilibili live publisher credentials are not configured"
            )

    def _cookie_header(self) -> str:
        parts = [
            f"SESSDATA={self._sessdata}",
            f"bili_jct={self._csrf}",
            f"DedeUserID={self._dede_user_id}",
        ]
        if self._dede_user_id_ckmd5:
            parts.append(f"DedeUserID__ckMd5={self._dede_user_id_ckmd5}")
        return "; ".join(parts)

    def _client(self, *, timeout: float = 30.0) -> httpx.Client:
        return httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Cookie": self._cookie_header(),
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/154 Safari/537.36"
                ),
                "Referer": "https://member.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
            },
        )

    @staticmethod
    def _marker(idempotency_key: str) -> str:
        return f"[shrimp-step10b:{idempotency_key}]"

    @staticmethod
    def _safe_error_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return {
                key: payload.get(key)
                for key in ("code", "message")
                if key in payload
            }
        return {"type": type(payload).__name__}

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Bilibili returned non-object JSON")
        return payload

    def validate_target(self, execution: dict[str, Any]) -> None:
        if execution.get("platform") != "BILIBILI":
            raise RuntimeError("Bilibili adapter requires platform=BILIBILI")
        expected_mid = str(execution.get("account_reference") or "").strip()
        if not expected_mid or not expected_mid.isdigit():
            raise RuntimeError(
                "Bilibili controlled target account_reference must be numeric MID"
            )

    def probe_account(self, *, expected_mid: str) -> dict[str, Any]:
        clean_mid = str(expected_mid or "").strip()
        if not clean_mid or not clean_mid.isdigit():
            raise RuntimeError("Bilibili health probe requires numeric expected MID")
        with self._client(timeout=15.0) as client:
            payload = self._response_json(
                client.get("https://api.bilibili.com/x/web-interface/nav")
            )
            if payload.get("code") != 0 or not isinstance(
                payload.get("data"), dict
            ):
                raise RuntimeError(
                    "Bilibili login health probe failed: "
                    + json.dumps(
                        self._safe_error_payload(payload),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            data = payload["data"]
            mid = str(data.get("mid") or "")
            is_login = bool(data.get("isLogin"))
            if not mid:
                raise RuntimeError("Bilibili health probe returned no MID")
            if self._dede_user_id != mid:
                raise RuntimeError(
                    "Configured DedeUserID does not match authenticated MID"
                )

            publish_probe_ok = False
            if is_login and mid == clean_mid:
                preupload = self._response_json(
                    client.get(
                        "https://member.bilibili.com/preupload",
                        params={"r": "probe"},
                    )
                )
                publish_probe_ok = isinstance(
                    preupload.get("lines"), list
                ) and bool(preupload.get("lines"))

            return {
                "mid": mid,
                "uname": str(data.get("uname") or ""),
                "is_login": is_login,
                "level": int(
                    data.get("level_info", {}).get("current_level") or 0
                ),
                "publish_probe_ok": publish_probe_ok,
            }

    def preflight(self, execution: dict[str, Any]) -> dict[str, Any]:
        self.validate_target(execution)
        with self._client(timeout=15.0) as client:
            payload = self._response_json(
                client.get("https://api.bilibili.com/x/web-interface/nav")
            )
            if payload.get("code") != 0 or not isinstance(
                payload.get("data"), dict
            ):
                raise RuntimeError(
                    "Bilibili login preflight failed: "
                    + json.dumps(
                        self._safe_error_payload(payload),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            data = payload["data"]
            mid = str(data.get("mid") or "")
            if not mid:
                raise RuntimeError("Bilibili preflight returned no MID")
            if mid != str(execution["account_reference"]):
                raise RuntimeError(
                    "Authenticated Bilibili MID does not match sacrificial target"
                )
            if self._dede_user_id != mid:
                raise RuntimeError(
                    "Configured DedeUserID does not match authenticated Bilibili MID"
                )
            return {
                "mid": mid,
                "uname": str(data.get("uname") or ""),
                "is_login": bool(data.get("isLogin")),
                "level": int(data.get("level_info", {}).get("current_level") or 0),
            }

    def _select_upos_line(
        self,
        client: httpx.Client,
    ) -> dict[str, str]:
        payload = self._response_json(
            client.get(
                "https://member.bilibili.com/preupload",
                params={"r": "probe"},
            )
        )
        lines = payload.get("lines")
        if not isinstance(lines, list):
            raise RuntimeError("Bilibili preupload probe returned no lines")
        for line in lines:
            if isinstance(line, dict) and line.get("os") == "upos":
                query = dict(parse_qsl(str(line.get("query") or "")))
                return query
        raise RuntimeError("Bilibili preupload probe returned no UPOS line")

    def _upload_media(
        self,
        client: httpx.Client,
        media_path: Path,
    ) -> dict[str, Any]:
        size = media_path.stat().st_size
        limit = max(
            1,
            int(settings.shrimp_bilibili_live_acceptance_max_media_bytes),
        )
        if size > limit:
            raise RuntimeError(
                f"Bilibili live acceptance media exceeds {limit} bytes"
            )

        line_query = self._select_upos_line(client)
        params: dict[str, Any] = {
            **line_query,
            "r": "upos",
            "profile": "ugcupos/bup",
            "ssl": 0,
            "version": "2.8.12",
            "build": 2081200,
            "name": media_path.name,
            "size": size,
        }
        pre = self._response_json(
            client.get("https://member.bilibili.com/preupload", params=params)
        )
        required = (
            "endpoint",
            "upos_uri",
            "auth",
            "chunk_size",
            "biz_id",
        )
        if any(key not in pre for key in required):
            raise RuntimeError("Bilibili preupload response is incomplete")

        endpoint = str(pre["endpoint"])
        if endpoint.startswith("//"):
            endpoint = "https:" + endpoint
        upos_uri = str(pre["upos_uri"])
        object_path = upos_uri.replace("upos://", "", 1)
        upload_url = endpoint.rstrip("/") + "/" + object_path.lstrip("/")
        headers = {"X-Upos-Auth": str(pre["auth"])}

        init = self._response_json(
            client.post(
                upload_url,
                params={"uploads": "", "output": "json"},
                headers=headers,
            )
        )
        upload_id = str(init.get("upload_id") or "")
        if not upload_id:
            raise RuntimeError("Bilibili UPOS returned no upload_id")

        chunk_size = int(pre["chunk_size"])
        chunks = int(math.ceil(size / chunk_size))
        parts: list[dict[str, Any]] = []

        with media_path.open("rb") as stream:
            for chunk in range(chunks):
                blob = stream.read(chunk_size)
                start = chunk * chunk_size
                end = start + len(blob)
                put = client.put(
                    upload_url,
                    params={
                        "uploadId": upload_id,
                        "chunks": chunks,
                        "total": size,
                        "chunk": chunk,
                        "size": len(blob),
                        "partNumber": chunk + 1,
                        "start": start,
                        "end": end,
                    },
                    headers=headers,
                    content=blob,
                )
                put.raise_for_status()
                parts.append({"partNumber": chunk + 1, "eTag": "etag"})

        completed = self._response_json(
            client.post(
                upload_url,
                params={
                    "name": media_path.name,
                    "uploadId": upload_id,
                    "biz_id": pre["biz_id"],
                    "output": "json",
                    "profile": "ugcupos/bup",
                },
                headers=headers,
                json={"parts": parts},
            )
        )
        if completed.get("OK") != 1:
            raise RuntimeError(
                "Bilibili UPOS finalize failed: "
                + json.dumps(
                    self._safe_error_payload(completed),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

        return {
            "filename": Path(object_path).stem,
            "cid": int(pre["biz_id"]),
            "upload_id": upload_id,
        }

    def _temporary_submit_payload(
        self,
        execution: dict[str, Any],
        upload: dict[str, Any],
        *,
        marker: str,
    ) -> dict[str, Any]:
        title = "Shrimp Step10B " + execution["execution_sha256"][:12]
        return {
            "videos": [{
                "filename": upload["filename"],
                "title": title[:80],
                "desc": "",
                "cid": upload["cid"],
            }],
            "cover": "",
            "cover43": "",
            "title": title[:80],
            "copyright": 1,
            "tid": int(settings.shrimp_bilibili_default_tid),
            "tag": "AI动画,测试",
            "desc_format_id": 9999,
            "desc": marker,
            "recreate": -1,
            "dynamic": "",
            "interactive": 0,
            "act_reserve_create": 0,
            "no_disturbance": 1,
            "no_reprint": 1,
            "subtitle": {"open": 0, "lan": ""},
            "dolby": 0,
            "lossless_music": 0,
            "up_selection_reply": False,
            "up_close_reply": True,
            "up_close_danmu": True,
            "web_os": 3,
            "is_only_self": 1,
            "csrf": self._csrf,
        }

    def upload(
        self,
        execution: dict[str, Any],
        *,
        media_path: Path,
        idempotency_key: str,
    ) -> UploadReceipt:
        self.validate_target(execution)
        marker = self._marker(idempotency_key)
        try:
            with self._client(timeout=60.0) as client:
                upload = self._upload_media(client, media_path)
                payload = self._temporary_submit_payload(
                    execution,
                    upload,
                    marker=marker,
                )
                response = self._response_json(
                    client.post(
                        "https://member.bilibili.com/x/vu/web/add/v3",
                        params={
                            "t": int(time.time() * 1000),
                            "csrf": self._csrf,
                        },
                        json=payload,
                    )
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PublisherWriteOutcomeUnknown(
                "Bilibili private upload response is ambiguous",
                phase="UPLOAD",
                error_type=type(exc).__name__,
                evidence_sha256=sha256_json({
                    "phase": "UPLOAD",
                    "marker": marker,
                    "error_type": type(exc).__name__,
                }),
            ) from exc

        if response.get("code") != 0:
            evidence = sha256_json({
                "phase": "UPLOAD",
                "marker": marker,
                "response": self._safe_error_payload(response),
            })
            raise PublisherWriteRejected(
                "Bilibili private upload was rejected",
                phase="UPLOAD",
                evidence_sha256=evidence,
            )
        data = response.get("data") or {}
        aid = str(data.get("aid") or "")
        bvid = str(data.get("bvid") or "")
        if not aid or not bvid:
            raise RuntimeError("Bilibili add/v3 returned no AID/BVID")
        return UploadReceipt(
            provider_upload_id=aid,
            state="UPLOADED_PRIVATE",
            provider_write_performed=True,
            metadata={
                "aid": aid,
                "bvid": bvid,
                "url": f"https://www.bilibili.com/video/{bvid}",
                "is_only_self": 1,
                "marker": marker,
            },
        )

    def _archive_view(
        self,
        client: httpx.Client,
        aid: str,
    ) -> dict[str, Any] | None:
        payload = self._response_json(
            client.get(
                "https://member.bilibili.com/x/web/archive/view",
                params={"aid": aid, "history": ""},
            )
        )
        if payload.get("code") != 0:
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    def _find_by_marker(
        self,
        client: httpx.Client,
        marker: str,
    ) -> dict[str, Any] | None:
        matches: list[dict[str, Any]] = []
        for status in ("pubed", "is_pubing", "not_pubed"):
            payload = self._response_json(
                client.get(
                    "https://member.bilibili.com/x/web/archives",
                    params={"status": status, "pn": 1},
                )
            )
            if payload.get("code") != 0:
                continue
            data = payload.get("data") or {}
            entries = data.get("arc_audits") or []
            for item in entries:
                archive = (
                    item.get("Archive")
                    if isinstance(item, dict)
                    else None
                )
                if not isinstance(archive, dict):
                    continue
                if marker in str(archive.get("desc") or ""):
                    matches.append(archive)
        unique = {
            str(item.get("aid")): item
            for item in matches
            if item.get("aid")
        }
        if len(unique) > 1:
            raise RuntimeError(
                "Bilibili upload reconciliation found multiple marker matches"
            )
        return next(iter(unique.values()), None)

    def reconcile_upload(
        self,
        execution: dict[str, Any],
        *,
        idempotency_key: str,
        provider_upload_id: str | None,
    ) -> UploadReceipt | None:
        marker = self._marker(idempotency_key)
        with self._client(timeout=20.0) as client:
            archive = None
            if provider_upload_id:
                view = self._archive_view(client, provider_upload_id)
                if view:
                    archive = view.get("archive") or view
            if archive is None:
                archive = self._find_by_marker(client, marker)
            if archive is None:
                return None
            aid = str(archive.get("aid") or "")
            bvid = str(archive.get("bvid") or "")
            if not aid:
                return None
            return UploadReceipt(
                provider_upload_id=aid,
                state="UPLOADED_PRIVATE",
                provider_write_performed=False,
                metadata={
                    "aid": aid,
                    "bvid": bvid,
                    "url": (
                        f"https://www.bilibili.com/video/{bvid}"
                        if bvid else None
                    ),
                    "marker": marker,
                },
            )

    def _final_edit_payload(
        self,
        execution: dict[str, Any],
        view: dict[str, Any],
        *,
        aid: str,
        marker: str,
    ) -> dict[str, Any]:
        archive = dict(view.get("archive") or {})
        videos = list(view.get("videos") or [])
        meta = dict(execution.get("publish_metadata") or {})
        title = str(meta.get("title") or archive.get("title") or "")[:80]
        description = str(meta.get("description") or "")
        description = (description + "\n\n" + marker).strip()[:2000]
        tags = meta.get("tags") or []
        tag = ",".join(str(item) for item in tags[:10]) or "AI动画,测试"
        category = meta.get("category")
        tid = (
            int(category)
            if str(category or "").isdigit()
            else int(settings.shrimp_bilibili_default_tid)
        )
        return {
            "aid": int(aid),
            "videos": [{
                "filename": item.get("filename", ""),
                "title": str(item.get("title") or title)[:80],
                "desc": str(item.get("desc") or ""),
                **(
                    {"cid": item.get("cid")}
                    if item.get("cid") is not None else {}
                ),
            } for item in videos],
            "title": title,
            "cover": archive.get("cover") or "",
            "cover43": "",
            "copyright": 1,
            "source": "",
            "tid": tid,
            "tag": tag,
            "desc_format_id": 9999,
            "desc": description,
            "recreate": -1,
            "dynamic": "",
            "interactive": 0,
            "act_reserve_create": 0,
            "no_disturbance": 1,
            "no_reprint": 1,
            "subtitle": {"open": 0, "lan": ""},
            "web_os": 3,
            "mission_id": 0,
            "is_only_self": 1,
            "csrf": self._csrf,
        }

    def publish(
        self,
        execution: dict[str, Any],
        *,
        provider_upload_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        self.validate_target(execution)
        marker = self._marker(execution["upload_idempotency_key"])
        try:
            with self._client(timeout=30.0) as client:
                view = self._archive_view(client, provider_upload_id)
                if view is None:
                    raise RuntimeError("Bilibili archive disappeared before edit")
                payload = self._final_edit_payload(
                    execution,
                    view,
                    aid=provider_upload_id,
                    marker=marker,
                )
                response = self._response_json(
                    client.post(
                        "https://member.bilibili.com/x/vu/web/edit",
                        params={
                            "t": int(time.time() * 1000),
                            "csrf": self._csrf,
                        },
                        json=payload,
                    )
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PublisherWriteOutcomeUnknown(
                "Bilibili private metadata finalize outcome is ambiguous",
                phase="PUBLISH",
                error_type=type(exc).__name__,
                evidence_sha256=sha256_json({
                    "phase": "PUBLISH",
                    "aid": provider_upload_id,
                    "error_type": type(exc).__name__,
                }),
            ) from exc

        if response.get("code") != 0:
            raise PublisherWriteRejected(
                "Bilibili metadata finalize was rejected",
                phase="PUBLISH",
                evidence_sha256=sha256_json({
                    "phase": "PUBLISH",
                    "aid": provider_upload_id,
                    "response": self._safe_error_payload(response),
                }),
            )
        data = response.get("data") or {}
        aid = str(data.get("aid") or provider_upload_id)
        bvid = str(data.get("bvid") or "")
        if not bvid:
            view = self.read_back_archive(execution, aid=aid, marker=marker)
            bvid = str(view.get("bvid") or "")
        return PublishReceipt(
            provider_publish_id=aid,
            url=(
                f"https://www.bilibili.com/video/{bvid}"
                if bvid else None
            ),
            state="PRIVATE",
            provider_write_performed=True,
            metadata={
                "aid": aid,
                "bvid": bvid,
                "is_only_self": 1,
                "marker": marker,
            },
        )

    def reconcile_publish(
        self,
        execution: dict[str, Any],
        *,
        idempotency_key: str,
        provider_publish_id: str | None,
    ) -> PublishReceipt | None:
        aid = (
            provider_publish_id
            or execution.get("provider_upload_id")
        )
        if not aid:
            return None
        marker = self._marker(execution["upload_idempotency_key"])
        try:
            readback = self.read_back_archive(
                execution,
                aid=str(aid),
                marker=marker,
            )
        except RuntimeError:
            return None
        if readback["is_only_self"] != 1:
            raise RuntimeError(
                "Bilibili reconciliation observed non-private visibility"
            )
        bvid = str(readback.get("bvid") or "")
        return PublishReceipt(
            provider_publish_id=str(aid),
            url=(
                f"https://www.bilibili.com/video/{bvid}"
                if bvid else None
            ),
            state="PRIVATE",
            provider_write_performed=False,
            metadata=readback,
        )

    def read_back_archive(
        self,
        execution: dict[str, Any],
        *,
        aid: str,
        marker: str,
    ) -> dict[str, Any]:
        with self._client(timeout=20.0) as client:
            view = self._archive_view(client, aid)
        if view is None:
            raise RuntimeError("Bilibili archive read-back returned no archive")
        archive = dict(view.get("archive") or {})
        desc = str(archive.get("desc") or "")
        if marker not in desc:
            raise RuntimeError(
                "Bilibili read-back reconciliation marker is missing"
            )
        is_only_self = int(archive.get("is_only_self") or 0)
        if is_only_self != 1:
            raise RuntimeError(
                "Bilibili read-back is not only-self/private"
            )
        owner_mid = str(
            archive.get("mid")
            or archive.get("author_mid")
            or execution.get("account_reference")
            or ""
        )
        if owner_mid and owner_mid != str(execution["account_reference"]):
            raise RuntimeError("Bilibili read-back owner MID drifted")
        return {
            "aid": str(archive.get("aid") or aid),
            "bvid": str(archive.get("bvid") or ""),
            "title": str(archive.get("title") or ""),
            "desc": desc,
            "state": archive.get("state"),
            "state_desc": str(archive.get("state_desc") or ""),
            "is_only_self": is_only_self,
            "owner_mid": owner_mid,
        }

    def delete_archive(
        self,
        execution: dict[str, Any],
        *,
        aid: str,
    ) -> dict[str, Any]:
        self.validate_target(execution)
        try:
            with self._client(timeout=20.0) as client:
                response = self._response_json(
                    client.post(
                        "https://member.bilibili.com/x/vu/web/archive/del",
                        params={"csrf": self._csrf},
                        data={"aid": aid, "csrf": self._csrf},
                    )
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PublisherWriteOutcomeUnknown(
                "Bilibili cleanup outcome is ambiguous",
                phase="CLEANUP",
                error_type=type(exc).__name__,
                evidence_sha256=sha256_json({
                    "phase": "CLEANUP",
                    "aid": aid,
                    "error_type": type(exc).__name__,
                }),
            ) from exc
        if response.get("code") != 0:
            raise PublisherWriteRejected(
                "Bilibili cleanup was rejected",
                phase="CLEANUP",
                evidence_sha256=sha256_json({
                    "phase": "CLEANUP",
                    "aid": aid,
                    "response": self._safe_error_payload(response),
                }),
            )
        return {"deleted": True, "aid": aid}

    def verify_deleted(
        self,
        execution: dict[str, Any],
        *,
        aid: str,
    ) -> bool:
        with self._client(timeout=20.0) as client:
            return self._archive_view(client, aid) is None
