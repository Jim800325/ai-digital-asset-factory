import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal")

def _address_is_public(value: str) -> bool:
    try:
        ip=ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )

def safe_url_syntax(url: str) -> bool:
    try:
        parsed=urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http","https") or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    host=parsed.hostname.lower().strip(".")
    if host=="localhost" or host.endswith(BLOCKED_HOST_SUFFIXES):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return _address_is_public(host)

def is_public_http_url(url: str) -> bool:
    if not safe_url_syntax(url):
        return False
    host=urlparse(url).hostname
    try:
        addresses=socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)
    except (OSError,socket.gaierror):
        return False
    resolved={entry[4][0] for entry in addresses if entry and entry[4]}
    return bool(resolved) and all(_address_is_public(value) for value in resolved)
