import hashlib

import pytest

from app import main


def test_preview_acceptance_attempt_digest_is_commit_scoped(monkeypatch):
    commit="a"*40
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA",commit)

    expected="preview-secret"
    digest=main._preview_acceptance_attempt_digest(expected)

    assert digest==hashlib.sha256(
        (expected+"\n"+commit).encode("utf-8")
    ).hexdigest()


def test_preview_acceptance_attempt_digest_changes_with_commit(monkeypatch):
    expected="preview-secret"

    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA","a"*40)
    first=main._preview_acceptance_attempt_digest(expected)

    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA","b"*40)
    second=main._preview_acceptance_attempt_digest(expected)

    assert first!=second


def test_preview_acceptance_attempt_digest_requires_full_commit(monkeypatch):
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA","short")

    with pytest.raises(
        RuntimeError,
        match="requires a full VERCEL_GIT_COMMIT_SHA",
    ):
        main._preview_acceptance_attempt_digest("preview-secret")
