import pytest

from app import db


def test_preview_requires_explicit_preview_database_url(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://production.example.invalid/prod",
    )
    monkeypatch.setattr(db.settings, "preview_database_url", "")

    with pytest.raises(RuntimeError, match="PREVIEW_DATABASE_URL is required"):
        db.database_selection()


def test_preview_never_selects_database_url(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://production.example.invalid/prod",
    )
    monkeypatch.setattr(
        db.settings,
        "preview_database_url",
        "postgresql://preview.example.invalid/preview",
    )

    selected = db.database_selection()

    assert selected["source"] == "PREVIEW_DATABASE_URL"
    assert selected["preview_isolated"] is True
    assert selected["vercel_env"] == "preview"
    assert selected["url"] == "postgresql://preview.example.invalid/preview"
    assert selected["url"] != "postgresql://production.example.invalid/prod"


def test_preview_only_feature_refuses_vercel_production(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setattr(
        db.settings,
        "deployment_authorization_preview_only",
        True,
    )

    with pytest.raises(RuntimeError, match="preview-only"):
        db.database_selection()


def test_non_vercel_keeps_normal_database_selection(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setattr(
        db.settings,
        "database_url",
        "postgresql://localhost/example",
    )

    selected = db.database_selection()

    assert selected["source"] == "DATABASE_URL"
    assert selected["preview_isolated"] is False
    assert selected["vercel_env"] == "non-vercel"
