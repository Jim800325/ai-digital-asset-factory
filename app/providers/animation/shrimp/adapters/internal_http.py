from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def validate_internal_base_url(
    base_url: str,
    *,
    allowed_hosts: list[str] | tuple[str, ...] = (),
) -> str:
    value = base_url.strip().rstrip("/")
    if not value:
        raise ValueError("Adapter base URL is required")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Adapter base URL must use http or https")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("Adapter base URL host is required")

    allow = {item.strip().lower() for item in allowed_hosts if item.strip()}
    if host in {"localhost"} or host in allow:
        return value

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(
            "Non-literal adapter hosts must be explicitly allowlisted"
        ) from None
    if not (ip.is_private or ip.is_loopback or ip.is_link_local):
        raise ValueError("Adapter endpoint must be private/loopback or allowlisted")
    return value


def post_json(
    url: str,
    payload: dict,
    *,
    timeout_seconds: float,
) -> tuple[bytes, dict[str, str]]:
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        body = response.read()
        headers = {key.lower(): value for key, value in response.headers.items()}
    return body, headers


def get_bytes(
    url: str,
    *,
    timeout_seconds: float,
) -> tuple[bytes, dict[str, str]]:
    request = Request(url, method="GET")
    with urlopen(request, timeout=timeout_seconds) as response:
        body = response.read()
        headers = {key.lower(): value for key, value in response.headers.items()}
    return body, headers
