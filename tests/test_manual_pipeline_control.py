from types import SimpleNamespace

import pytest

import app.manual_pipeline as manual


class FakeRedis:
    def ping(self):
        return True


class FakeWorker:
    def __init__(self, state="idle", queues=None):
        self._state = state
        self._queues = queues or ["asset-factory"]

    def get_state(self):
        return self._state

    def queue_names(self):
        return list(self._queues)


def test_manual_pipeline_defaults_fail_closed(monkeypatch):
    monkeypatch.setattr(manual.settings, "manual_pipeline_execution_enabled", False)
    monkeypatch.setattr(manual.settings, "manual_pipeline_execution_key", "")
    monkeypatch.setattr(manual, "_redis", lambda: FakeRedis())
    monkeypatch.setattr(manual.Worker, "all", lambda connection: [FakeWorker()])

    result = manual.manual_pipeline_readiness()

    assert result["status"] == "BLOCKED"
    assert "MANUAL_PIPELINE_EXECUTION_DISABLED" in result["blockers"]
    assert "MANUAL_PIPELINE_EXECUTION_KEY" in result["blockers"]
    assert result["production_provider_writes"] is False
    assert result["automatic_publish"] is False


def test_manual_pipeline_requires_correct_queue_worker(monkeypatch):
    monkeypatch.setattr(manual.settings, "manual_pipeline_execution_enabled", True)
    monkeypatch.setattr(manual.settings, "manual_pipeline_execution_key", "secret")
    monkeypatch.setattr(manual, "_redis", lambda: FakeRedis())
    monkeypatch.setattr(
        manual.Worker,
        "all",
        lambda connection: [FakeWorker(queues=["different-queue"])],
    )

    result = manual.manual_pipeline_readiness()

    assert result["status"] == "BLOCKED"
    assert result["active_worker_count"] == 0
    assert "RQ_WORKER_UNAVAILABLE" in result["blockers"]


def test_manual_pipeline_ready_with_live_worker(monkeypatch):
    monkeypatch.setattr(manual.settings, "manual_pipeline_execution_enabled", True)
    monkeypatch.setattr(manual.settings, "manual_pipeline_execution_key", "secret")
    monkeypatch.setattr(manual, "_redis", lambda: FakeRedis())
    monkeypatch.setattr(manual.Worker, "all", lambda connection: [FakeWorker()])

    result = manual.manual_pipeline_readiness()

    assert result["status"] == "READY"
    assert result["active_worker_count"] == 1
    assert result["blockers"] == []


def test_manual_pipeline_rejects_bad_key(monkeypatch):
    monkeypatch.setattr(manual.settings, "manual_pipeline_execution_key", "expected")
    with pytest.raises(PermissionError):
        manual._require_key("wrong")


def test_manual_job_normalizes_finished_enum(monkeypatch):
    class FakeStatus:
        value = "finished"

    class FakeJob:
        id = "job-1"
        enqueued_at = None
        started_at = None
        ended_at = None
        result = {"run_id": "run-1", "evidence": 3, "opportunities": 1}

        def get_status(self, refresh=True):
            return FakeStatus()

    monkeypatch.setattr(manual, "_redis", lambda: FakeRedis())
    monkeypatch.setattr(manual.Job, "fetch", lambda job_id, connection: FakeJob())

    result = manual.manual_pipeline_job("job-1")

    assert result["status"] == "FINISHED"
    assert result["run_id"] == "run-1"
    assert result["result"]["evidence"] == 3
