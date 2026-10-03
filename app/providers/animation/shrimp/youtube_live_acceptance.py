from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text

from app.db import engine
from app.providers.animation.shrimp.publisher_execution import (
    get_publish_execution,
    publish_uploaded_media,
    reconcile_publish_upload,
    reconcile_published_media,
    upload_publish_media,
)
from app.providers.animation.shrimp.publisher_execution_adapter import (
    PublisherWriteOutcomeUnknown,
)
from app.providers.animation.shrimp.youtube_live_publisher import (
    YouTubeLivePublisherAdapter,
)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _serialize(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("id", "execution_id"):
        if key in result and result[key] is not None:
            result[key] = str(result[key])
    return result


def _marker(upload_idempotency_key: str) -> str:
    return f"[shrimp-step10a:{upload_idempotency_key}]"


def _get_run_locked(db, execution_id) -> dict[str, Any] | None:
    row = db.execute(
        text("""
          SELECT *
          FROM shrimp_animation_youtube_live_acceptance_runs
          WHERE execution_id=CAST(:execution_id AS uuid)
          FOR UPDATE
        """),
        {"execution_id": execution_id},
    ).mappings().one_or_none()
    return dict(row) if row else None


def _merge_evidence(
    current: dict[str, Any] | None,
    section: str,
    value: Any,
) -> dict[str, Any]:
    result = dict(current or {})
    result[section] = value
    return result


def _upsert_failure(
    execution_id,
    *,
    actor: str,
    exc: Exception,
) -> None:
    evidence_sha = _sha(
        {
            "error_type": type(exc).__name__,
            "message": str(exc)[:2000],
        }
    )
    with engine.begin() as db:
        run = _get_run_locked(db, execution_id)
        if run is None:
            return
        if run["acceptance_status"] == "CLEANED_UP":
            return
        db.execute(
            text("""
              UPDATE shrimp_animation_youtube_live_acceptance_runs
              SET acceptance_status='FAILED',
                  failure_type=:failure_type,
                  failure_evidence_sha256=:failure_sha,
                  evidence=CAST(:evidence AS jsonb),
                  finished_at=now()
              WHERE id=:id
            """),
            {
                "id": run["id"],
                "failure_type": type(exc).__name__[:200],
                "failure_sha": evidence_sha,
                "evidence": _json(
                    _merge_evidence(
                        run.get("evidence"),
                        "failure",
                        {
                            "actor": actor,
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:2000],
                            "evidence_sha256": evidence_sha,
                        },
                    )
                ),
            },
        )


def get_youtube_live_acceptance(execution_id) -> dict[str, Any] | None:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_youtube_live_acceptance_runs
              WHERE execution_id=CAST(:execution_id AS uuid)
            """),
            {"execution_id": execution_id},
        ).mappings().one_or_none()
    return _serialize(row) if row else None


def run_youtube_live_acceptance(
    execution_id,
    *,
    actor: str = "shrimp-youtube-live-acceptance-api",
    adapter: YouTubeLivePublisherAdapter | None = None,
) -> dict[str, Any]:
    clean_actor = (
        (actor or "").strip() or "shrimp-youtube-live-acceptance-api"
    )
    chosen = adapter or YouTubeLivePublisherAdapter()
    execution = get_publish_execution(execution_id)
    if execution["platform"] != "YOUTUBE":
        raise RuntimeError("Step 10A only accepts YOUTUBE executions")
    if execution["execution_adapter"] != "YOUTUBE_CONTROLLED":
        raise RuntimeError(
            "Step 10A requires execution_adapter=YOUTUBE_CONTROLLED"
        )
    if execution["source_stale"]:
        raise RuntimeError("Step 10A refuses to begin from a STALE source")
    marker = _marker(execution["upload_idempotency_key"])

    with engine.begin() as db:
        run = _get_run_locked(db, execution_id)
        if run is None:
            row = db.execute(
                text("""
                  INSERT INTO shrimp_animation_youtube_live_acceptance_runs(
                    execution_id,acceptance_status,expected_channel_id,
                    reconciliation_marker,evidence,created_by)
                  VALUES(
                    CAST(:execution_id AS uuid),'CREATED',:expected_channel_id,
                    :marker,'{}'::jsonb,:actor)
                  RETURNING *
                """),
                {
                    "execution_id": execution_id,
                    "expected_channel_id": execution["account_reference"],
                    "marker": marker,
                    "actor": clean_actor[:200],
                },
            ).mappings().one()
            run = dict(row)
        elif run["acceptance_status"] == "CLEANED_UP":
            result = _serialize(run)
            result["replayed"] = True
            return result

    try:
        preflight = chosen.preflight(execution)
        if preflight["channel_id"] != execution["account_reference"]:
            raise RuntimeError(
                "Step 10A authenticated channel does not match execution"
            )
        with engine.begin() as db:
            run = _get_run_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_youtube_live_acceptance_runs
                  SET acceptance_status='PRECHECKED',
                      actual_channel_id=:actual_channel_id,
                      evidence=CAST(:evidence AS jsonb)
                  WHERE id=:id
                """),
                {
                    "id": run["id"],
                    "actual_channel_id": preflight["channel_id"],
                    "evidence": _json(
                        _merge_evidence(
                            run.get("evidence"),
                            "preflight",
                            {
                                "channel_id": preflight["channel_id"],
                                "channel_title": preflight.get("channel_title"),
                                "category_id": preflight["category_id"],
                                "uploads_playlist_present": bool(
                                    preflight.get("uploads_playlist_id")
                                ),
                                "private_only": True,
                            },
                        )
                    ),
                },
            )

        execution = get_publish_execution(execution_id)
        if execution["execution_status"] == "SNAPSHOT_CREATED":
            try:
                upload_publish_media(
                    execution_id,
                    actor=clean_actor,
                    adapter=chosen,
                )
            except RuntimeError:
                execution = get_publish_execution(execution_id)
                if execution["execution_status"] != "UPLOAD_UNKNOWN":
                    raise
                reconcile_publish_upload(
                    execution_id,
                    actor=clean_actor,
                    adapter=chosen,
                )

        execution = get_publish_execution(execution_id)
        if execution["execution_status"] == "UPLOAD_UNKNOWN":
            reconcile_publish_upload(
                execution_id,
                actor=clean_actor,
                adapter=chosen,
            )
            execution = get_publish_execution(execution_id)
        if execution["execution_status"] != "UPLOADED":
            raise RuntimeError(
                "Step 10A upload did not reconcile to UPLOADED"
            )
        if not execution.get("provider_upload_id"):
            raise RuntimeError("Step 10A upload has no provider video ID")

        with engine.begin() as db:
            run = _get_run_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_youtube_live_acceptance_runs
                  SET acceptance_status='UPLOADED',
                      video_id=COALESCE(video_id,:video_id),
                      external_upload_performed=true,
                      evidence=CAST(:evidence AS jsonb)
                  WHERE id=:id
                """),
                {
                    "id": run["id"],
                    "video_id": execution["provider_upload_id"],
                    "evidence": _json(
                        _merge_evidence(
                            run.get("evidence"),
                            "upload",
                            {
                                "video_id": execution["provider_upload_id"],
                                "upload_write_count":
                                    execution["upload_write_count"],
                                "upload_outcome":
                                    execution["upload_outcome"],
                            },
                        )
                    ),
                },
            )

        if execution["execution_status"] == "UPLOADED":
            try:
                publish_uploaded_media(
                    execution_id,
                    actor=clean_actor,
                    adapter=chosen,
                )
            except RuntimeError:
                execution = get_publish_execution(execution_id)
                if execution["execution_status"] != "PUBLISH_UNKNOWN":
                    raise
                reconcile_published_media(
                    execution_id,
                    actor=clean_actor,
                    adapter=chosen,
                )

        execution = get_publish_execution(execution_id)
        if execution["execution_status"] == "PUBLISH_UNKNOWN":
            reconcile_published_media(
                execution_id,
                actor=clean_actor,
                adapter=chosen,
            )
            execution = get_publish_execution(execution_id)
        if execution["execution_status"] != "PUBLISHED":
            raise RuntimeError(
                "Step 10A publish did not reconcile to PUBLISHED"
            )

        video_id = str(
            execution.get("provider_publish_id")
            or execution.get("provider_upload_id")
            or ""
        )
        if not video_id:
            raise RuntimeError("Step 10A publish has no provider video ID")

        with engine.begin() as db:
            run = _get_run_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_youtube_live_acceptance_runs
                  SET acceptance_status='PUBLISHED',
                      video_id=COALESCE(video_id,:video_id),
                      provider_video_url=COALESCE(
                        provider_video_url,:provider_video_url
                      ),
                      external_publish_performed=true,
                      evidence=CAST(:evidence AS jsonb)
                  WHERE id=:id
                """),
                {
                    "id": run["id"],
                    "video_id": video_id,
                    "provider_video_url":
                        execution.get("provider_publish_url"),
                    "evidence": _json(
                        _merge_evidence(
                            run.get("evidence"),
                            "publish",
                            {
                                "video_id": video_id,
                                "publish_write_count":
                                    execution["publish_write_count"],
                                "publish_outcome":
                                    execution["publish_outcome"],
                                "provider_video_url":
                                    execution.get("provider_publish_url"),
                            },
                        )
                    ),
                },
            )

        readback = chosen.read_back_video(
            execution,
            video_id=video_id,
            marker=marker,
        )
        if readback["privacy_status"] != "private":
            raise RuntimeError(
                "Step 10A provider read-back is not PRIVATE"
            )
        with engine.begin() as db:
            run = _get_run_locked(db, execution_id)
            db.execute(
                text("""
                  UPDATE shrimp_animation_youtube_live_acceptance_runs
                  SET acceptance_status='VERIFIED_PRIVATE',
                      privacy_status=:privacy_status,
                      processing_status=:processing_status,
                      provider_read_back_verified=true,
                      private_visibility_verified=true,
                      evidence=CAST(:evidence AS jsonb)
                  WHERE id=:id
                """),
                {
                    "id": run["id"],
                    "privacy_status": readback["privacy_status"],
                    "processing_status": readback.get("processing_status"),
                    "evidence": _json(
                        _merge_evidence(
                            run.get("evidence"),
                            "read_back",
                            {
                                "video_id": readback["video_id"],
                                "channel_id": readback["channel_id"],
                                "title": readback["title"],
                                "category_id": readback["category_id"],
                                "privacy_status":
                                    readback["privacy_status"],
                                "processing_status":
                                    readback.get("processing_status"),
                                "marker_verified": True,
                            },
                        )
                    ),
                },
            )

        with engine.begin() as db:
            run = _get_run_locked(db, execution_id)
            if run["cleanup_write_count"] == 0:
                db.execute(
                    text("""
                      UPDATE shrimp_animation_youtube_live_acceptance_runs
                      SET cleanup_write_count=1,
                          cleanup_outcome='REQUESTED'
                      WHERE id=:id
                    """),
                    {"id": run["id"]},
                )
                do_cleanup = True
            else:
                do_cleanup = False

        cleanup_ambiguous = False
        if do_cleanup:
            try:
                cleanup_result = chosen.delete_video(
                    execution,
                    video_id=video_id,
                )
                with engine.begin() as db:
                    run = _get_run_locked(db, execution_id)
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_youtube_live_acceptance_runs
                          SET cleanup_outcome='ACCEPTED',
                              cleanup_performed=true,
                              evidence=CAST(:evidence AS jsonb)
                          WHERE id=:id
                        """),
                        {
                            "id": run["id"],
                            "evidence": _json(
                                _merge_evidence(
                                    run.get("evidence"),
                                    "cleanup_write",
                                    cleanup_result,
                                )
                            ),
                        },
                    )
            except PublisherWriteOutcomeUnknown as exc:
                cleanup_ambiguous = True
                with engine.begin() as db:
                    run = _get_run_locked(db, execution_id)
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_youtube_live_acceptance_runs
                          SET acceptance_status='CLEANUP_UNKNOWN',
                              cleanup_outcome='AMBIGUOUS',
                              failure_type=:failure_type,
                              failure_evidence_sha256=:failure_sha,
                              evidence=CAST(:evidence AS jsonb)
                          WHERE id=:id
                        """),
                        {
                            "id": run["id"],
                            "failure_type": exc.error_type[:200],
                            "failure_sha": exc.evidence_sha256,
                            "evidence": _json(
                                _merge_evidence(
                                    run.get("evidence"),
                                    "cleanup_ambiguous",
                                    {
                                        "error_type": exc.error_type,
                                        "evidence_sha256":
                                            exc.evidence_sha256,
                                        "write_replay_forbidden": True,
                                    },
                                )
                            ),
                        },
                    )

        deleted = chosen.verify_deleted(
            execution,
            video_id=video_id,
        )
        with engine.begin() as db:
            run = _get_run_locked(db, execution_id)
            if deleted:
                outcome = (
                    "RECONCILED_DELETED"
                    if cleanup_ambiguous
                    else run["cleanup_outcome"]
                )
                db.execute(
                    text("""
                      UPDATE shrimp_animation_youtube_live_acceptance_runs
                      SET acceptance_status='CLEANED_UP',
                          cleanup_outcome=:cleanup_outcome,
                          cleanup_verified=true,
                          cleanup_performed=true,
                          evidence=CAST(:evidence AS jsonb),
                          finished_at=now()
                      WHERE id=:id
                    """),
                    {
                        "id": run["id"],
                        "cleanup_outcome": outcome,
                        "evidence": _json(
                            _merge_evidence(
                                run.get("evidence"),
                                "cleanup_read_back",
                                {
                                    "video_id": video_id,
                                    "deleted": True,
                                    "provider_write_replayed": False,
                                },
                            )
                        ),
                    },
                )
            else:
                db.execute(
                    text("""
                      UPDATE shrimp_animation_youtube_live_acceptance_runs
                      SET acceptance_status='CLEANUP_UNKNOWN',
                          cleanup_outcome='RECONCILED_PRESENT',
                          evidence=CAST(:evidence AS jsonb)
                      WHERE id=:id
                    """),
                    {
                        "id": run["id"],
                        "evidence": _json(
                            _merge_evidence(
                                run.get("evidence"),
                                "cleanup_read_back",
                                {
                                    "video_id": video_id,
                                    "deleted": False,
                                    "second_delete_forbidden": True,
                                },
                            )
                        ),
                    },
                )

        result = get_youtube_live_acceptance(execution_id)
        if result is None:
            raise RuntimeError("Step 10A acceptance audit disappeared")
        result["replayed"] = False
        return result

    except Exception as exc:
        _upsert_failure(
            execution_id,
            actor=clean_actor,
            exc=exc,
        )
        raise
