from __future__ import annotations

import secrets
from datetime import datetime, timezone
from uuid import UUID

from redis import Redis
from rq import Queue, Worker
from rq.job import Job

from app.config import settings
from app.db_reliability import database_health
from app.migrate import migration_status
from app.validation_expansion import (
    execute_validation_expansion,
    get_validation_expansion_run,
)
from app.validation_search_plan import build_validation_search_plan

QUEUE_NAME="asset-factory"
TERMINAL={"finished","failed","stopped","canceled"}


def _redis() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=3,
        socket_timeout=3,
    )


def validation_expansion_readiness() -> dict:
    blockers=[]
    key_configured=bool(settings.validation_expansion_key.strip())
    db_state=database_health()
    migrations=migration_status() if db_state["available"] else {
        "status":"DB_UNAVAILABLE",
        "pending":None,
    }

    redis_available=False
    active_workers=0
    try:
        conn=_redis()
        redis_available=bool(conn.ping())
        if redis_available:
            workers=Worker.all(connection=conn)
            active_workers=sum(
                1 for worker in workers
                if worker.get_state() in {"idle","busy"}
                and QUEUE_NAME in set(worker.queue_names())
            )
    except Exception:
        redis_available=False

    if not settings.validation_expansion_enabled:
        blockers.append("VALIDATION_EXPANSION_DISABLED")
    if not key_configured:
        blockers.append("VALIDATION_EXPANSION_KEY")
    if not db_state["available"]:
        blockers.append("DATABASE_UNAVAILABLE")
    if migrations.get("status")!="CURRENT":
        blockers.append("MIGRATIONS_PENDING")
    if not redis_available:
        blockers.append("REDIS_UNAVAILABLE")
    if active_workers<1:
        blockers.append("RQ_WORKER_UNAVAILABLE")

    return {
        "status":"READY" if not blockers else "BLOCKED",
        "mode":"CONTROLLED_VALIDATION_EXPANSION",
        "execution_enabled":settings.validation_expansion_enabled,
        "key_configured":key_configured,
        "database":db_state.get("status"),
        "migrations":migrations.get("status"),
        "redis_available":redis_available,
        "active_worker_count":active_workers,
        "queue":QUEUE_NAME,
        "blockers":blockers,
        "max_queries":min(max(int(settings.validation_expansion_max_queries),1),12),
        "results_per_query":min(max(int(settings.validation_expansion_results_per_query),1),10),
        "max_accepted":min(max(int(settings.validation_expansion_max_accepted),1),5),
        "automatic_build_approval":False,
        "automatic_build_execution":False,
        "production_provider_writes":False,
        "automatic_publish":False,
    }


def _require_key(provided: str | None) -> None:
    expected=settings.validation_expansion_key.strip()
    if not expected:
        raise PermissionError("Validation expansion key is not configured")
    if not provided or not secrets.compare_digest(provided,expected):
        raise PermissionError("Invalid validation expansion key")


def enqueue_validation_expansion(
    opportunity_id: UUID,
    *,
    provided_key: str | None,
    requested_by: str = "controlled-api",
) -> dict:
    _require_key(provided_key)
    readiness=validation_expansion_readiness()
    if readiness["status"]!="READY":
        raise RuntimeError(
            "Validation expansion is blocked: "+",".join(readiness["blockers"])
        )

    plan=build_validation_search_plan(opportunity_id)
    if not plan["eligibility"]["recommended_for_expansion"]:
        raise RuntimeError(
            "Opportunity is not eligible for validation expansion: "
            + ",".join(plan["eligibility"]["reasons"])
        )

    queue=Queue(QUEUE_NAME,connection=_redis())
    job=queue.enqueue(
        execute_validation_expansion,
        str(opportunity_id),
        requested_by=requested_by[:200],
        job_timeout=300,
        result_ttl=3600,
        failure_ttl=86400,
    )
    return {
        "job_id":job.id,
        "status":"QUEUED",
        "queued_at":datetime.now(timezone.utc).isoformat(),
        "queue":QUEUE_NAME,
        "opportunity_id":str(opportunity_id),
        "plan_version":plan["version"],
        "search_seed":plan["search_seed"],
        "target_dimensions":[task["dimension"] for task in plan["tasks"]],
    }


def validation_expansion_job(job_id: str) -> dict:
    job=Job.fetch(job_id,connection=_redis())
    raw=job.get_status(refresh=True)
    status=getattr(raw,"value",str(raw)).lower()
    payload={
        "job_id":job.id,
        "status":status.upper(),
        "enqueued_at":job.enqueued_at.isoformat() if job.enqueued_at else None,
        "started_at":job.started_at.isoformat() if job.started_at else None,
        "ended_at":job.ended_at.isoformat() if job.ended_at else None,
        "run_id":None,
        "result":None,
        "error":None,
    }
    if status=="finished" and isinstance(job.result,dict):
        payload["result"]=job.result
        payload["run_id"]=job.result.get("run_id")
    elif status in TERMINAL:
        payload["error"]="Validation expansion job did not complete successfully"
    return payload
