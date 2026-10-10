import secrets
from datetime import datetime, timezone

from redis import Redis
from rq import Queue, Worker
from rq.job import Job

from app.config import settings
from app.workers.pipeline import run_pipeline

QUEUE_NAME = "asset-factory"
TERMINAL = {"finished", "failed", "stopped", "canceled"}


def _redis() -> Redis:
    return Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3)


def manual_pipeline_readiness() -> dict:
    blockers = []
    redis_available = False
    active_workers = 0
    key_configured = bool(settings.manual_pipeline_execution_key.strip())
    try:
        conn = _redis()
        redis_available = bool(conn.ping())
        if redis_available:
            workers = Worker.all(connection=conn)
            active_workers = sum(
                1
                for w in workers
                if w.get_state() in {"idle", "busy"}
                and QUEUE_NAME in set(w.queue_names())
            )
    except Exception:
        redis_available = False
        active_workers = 0

    if not settings.manual_pipeline_execution_enabled:
        blockers.append("MANUAL_PIPELINE_EXECUTION_DISABLED")
    if not key_configured:
        blockers.append("MANUAL_PIPELINE_EXECUTION_KEY")
    if not redis_available:
        blockers.append("REDIS_UNAVAILABLE")
    if active_workers < 1:
        blockers.append("RQ_WORKER_UNAVAILABLE")

    return {
        "status": "READY" if not blockers else "BLOCKED",
        "mode": "MANUAL_PIPELINE",
        "execution_enabled": settings.manual_pipeline_execution_enabled,
        "key_configured": key_configured,
        "redis_available": redis_available,
        "active_worker_count": active_workers,
        "queue": QUEUE_NAME,
        "blockers": blockers,
        "production_provider_writes": False,
        "automatic_publish": False,
    }


def _require_key(provided: str | None) -> None:
    expected = settings.manual_pipeline_execution_key.strip()
    if not expected:
        raise PermissionError("Manual pipeline execution key is not configured")
    if not provided or not secrets.compare_digest(provided, expected):
        raise PermissionError("Invalid manual pipeline execution key")


def enqueue_manual_pipeline(provided_key: str | None) -> dict:
    _require_key(provided_key)
    readiness = manual_pipeline_readiness()
    if readiness["status"] != "READY":
        raise RuntimeError("Manual pipeline is blocked: " + ",".join(readiness["blockers"]))
    conn = _redis()
    q = Queue(QUEUE_NAME, connection=conn)
    job = q.enqueue(run_pipeline, job_timeout=900, result_ttl=3600, failure_ttl=86400)
    return {
        "job_id": job.id,
        "status": "QUEUED",
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "queue": QUEUE_NAME,
    }


def manual_pipeline_job(job_id: str) -> dict:
    conn = _redis()
    job = Job.fetch(job_id, connection=conn)
    raw_status = job.get_status(refresh=True)
    status = getattr(raw_status, "value", str(raw_status)).lower()
    payload = {
        "job_id": job.id,
        "status": status.upper(),
        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
        "run_id": None,
        "result": None,
        "error": None,
    }
    if status == "finished" and isinstance(job.result, dict):
        payload["result"] = job.result
        payload["run_id"] = job.result.get("run_id")
    elif status in {"failed", "stopped", "canceled"}:
        payload["error"] = "Pipeline job did not complete successfully"
    return payload
