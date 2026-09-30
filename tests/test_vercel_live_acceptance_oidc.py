import pytest

from app import vercel_live_acceptance as live


def test_require_oidc_prefers_explicit_request_token(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_OIDC_TOKEN", "preview-system-token")

    assert live._require_oidc("request-token") == "request-token"


def test_require_oidc_uses_system_token_only_in_preview(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_OIDC_TOKEN", "preview-system-token")

    assert live._require_oidc(None) == "preview-system-token"


def test_require_oidc_remains_fail_closed_outside_preview(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("VERCEL_OIDC_TOKEN", "production-system-token")

    with pytest.raises(live.LiveAcceptanceError, match="OIDC token is unavailable"):
        live._require_oidc(None)
