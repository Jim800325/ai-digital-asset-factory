from sqlalchemy.exc import OperationalError

import app.db_reliability as dbrel


def _op_error():
    return OperationalError(
        "select 1",
        {},
        OSError(16, "Device or resource busy"),
        connection_invalidated=True,
    )


def test_read_with_retry_recovers_from_transient_connectivity(monkeypatch):
    monkeypatch.setattr(dbrel.settings, "database_read_retry_attempts", 2)
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _op_error()
        return "ok"

    assert dbrel.read_with_retry("test_read", fn) == "ok"
    assert calls["count"] == 2


def test_read_with_retry_fails_with_explicit_database_state(monkeypatch):
    monkeypatch.setattr(dbrel.settings, "database_read_retry_attempts", 2)
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        raise _op_error()

    try:
        dbrel.read_with_retry("test_read", fn)
    except dbrel.DatabaseUnavailable as exc:
        assert exc.operation == "test_read"
    else:
        raise AssertionError("DatabaseUnavailable was not raised")
    assert calls["count"] == 2


def test_release_database_payload_is_fail_closed_and_not_retry_safe():
    payload = dbrel.db_unavailable_payload(
        operation="release_decision",
        approval_sensitive=True,
    )

    assert payload["status"] == "DB_UNAVAILABLE"
    assert payload["approval_mode"] == "APPROVAL_FAIL_CLOSED"
    assert payload["retry_safe"] is False


def test_read_database_payload_is_degraded_but_retry_safe():
    payload = dbrel.db_unavailable_payload(
        operation="review_workspace_list",
        approval_sensitive=False,
    )

    assert payload["status"] == "DB_UNAVAILABLE"
    assert payload["approval_mode"] == "READ_DEGRADED"
    assert payload["retry_safe"] is True
