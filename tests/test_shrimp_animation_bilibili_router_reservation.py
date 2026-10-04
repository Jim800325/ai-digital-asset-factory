from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.shrimp.bilibili_router import (
    create_pre_publish_reservation,
    get_reservation,
    rebind_pre_publish_reservation,
)


def test_router_reservation_service_contract(monkeypatch):
    monkeypatch.setattr(settings,"shrimp_bilibili_reservation_ttl_minutes",15)

    with engine.begin() as db:
        job=db.execute(text("""
          SELECT provider_job_id
          FROM shrimp_animation_jobs
          ORDER BY created_at DESC
          LIMIT 1
        """)).mappings().one_or_none()

    if job is None:
        return

    try:
        first=create_pre_publish_reservation(
            job["provider_job_id"],
            actor="ci-router-service",
        )
    except RuntimeError as exc:
        assert "healthy Bilibili" in str(exc) or "reserved" in str(exc)
        return

    stored=get_reservation(first["id"])
    assert stored["reservation_status"]=="HELD"
    assert stored["expires_at"]>datetime.now(timezone.utc).isoformat()
    assert len(stored["selection_sha256"])==64

    try:
        rebound=rebind_pre_publish_reservation(
            first["id"],
            actor="ci-router-rebind",
        )
    except RuntimeError as exc:
        assert "healthy Bilibili" in str(exc) or "reserved" in str(exc)
        return

    assert rebound["requires_new_step9_plan"] is True
    assert rebound["rebound_from_reservation_id"]==first["id"]
