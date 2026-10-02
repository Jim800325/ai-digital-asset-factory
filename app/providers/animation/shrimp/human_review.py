from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json


REQUIRED_REVIEW_CHECKLIST = (
    "watch_full_episode",
    "verify_dialogue_and_subtitles",
    "verify_visual_continuity",
    "verify_rights_and_provenance",
    "approve_release_intent",
)


@dataclass(frozen=True)
class ReviewFile:
    path: Path
    filename: str
    media_type: str
    sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise PermissionError("Human review accepts only local file URI artifacts")
    if parsed.netloc not in ("", "localhost"):
        raise PermissionError("Remote file URI hosts are not allowed")
    return Path(url2pathname(unquote(parsed.path))).resolve()


def _load_job(job_id, *, for_update: bool = False) -> dict:
    suffix = " FOR UPDATE OF saj,pj" if for_update else ""
    with engine.connect() if not for_update else engine.begin() as db:
        row = db.execute(
            text(
                """
                SELECT saj.provider_job_id,saj.episode_id,saj.review_status,
                       saj.episode_bundle_sha256,
                       saj.release_review_package_sha256,
                       saj.reviewed_at,saj.updated_at,
                       pj.job_status,pj.publish_enabled,
                       pj.production_execution_enabled,
                       pj.external_side_effects,pj.completed_at
                FROM shrimp_animation_jobs saj
                JOIN production_provider_jobs pj
                  ON pj.id=saj.provider_job_id
                WHERE saj.provider_job_id=CAST(:job_id AS uuid)
                """
                + suffix
            ),
            {"job_id": job_id},
        ).mappings().one_or_none()
    if row is None:
        raise LookupError("Shrimp animation job not found")
    return dict(row)


def _latest_bundle(db, job_id, *, current_only: bool = False) -> dict | None:
    sql = """
      SELECT *
      FROM shrimp_animation_episode_bundles
      WHERE provider_job_id=CAST(:job_id AS uuid)
    """
    if current_only:
        sql += " AND bundle_status='CURRENT'"
    sql += " ORDER BY created_at DESC LIMIT 1"
    row = db.execute(text(sql), {"job_id": job_id}).mappings().one_or_none()
    return dict(row) if row else None


def _latest_review_package(
    db,
    job_id,
    *,
    current_only: bool = False,
) -> dict | None:
    sql = """
      SELECT *
      FROM shrimp_animation_release_review_packages
      WHERE provider_job_id=CAST(:job_id AS uuid)
    """
    if current_only:
        sql += " AND review_status='READY_FOR_HUMAN_REVIEW'"
    sql += " ORDER BY created_at DESC LIMIT 1"
    row = db.execute(text(sql), {"job_id": job_id}).mappings().one_or_none()
    return dict(row) if row else None


def _latest_qc(db, job_id) -> dict | None:
    row = db.execute(
        text("""
          SELECT id,report_sha256,report_content,passed,
                 hard_failure_count,qc_status,created_at
          FROM shrimp_animation_qc_reports
          WHERE provider_job_id=CAST(:job_id AS uuid)
          ORDER BY created_at DESC
          LIMIT 1
        """),
        {"job_id": job_id},
    ).mappings().one_or_none()
    return dict(row) if row else None


def _decisions(db, job_id) -> list[dict]:
    return [
        dict(row)
        for row in db.execute(
            text("""
              SELECT id,decision,reason,actor,
                     episode_bundle_sha256,
                     release_review_package_sha256,
                     confirmed_checklist,decision_sha256,
                     decision_status,decided_at,superseded_at
              FROM shrimp_animation_review_decisions
              WHERE provider_job_id=CAST(:job_id AS uuid)
              ORDER BY decided_at DESC,id DESC
            """),
            {"job_id": job_id},
        ).mappings().all()
    ]


def _artifact_item(package_content: dict, relative_path: str) -> dict | None:
    for item in (package_content.get("bundle") or {}).get("artifacts") or []:
        if item.get("relative_path") == relative_path:
            return dict(item)
    return None


def _package_integrity(
    job: dict,
    bundle: dict | None,
    review: dict | None,
    qc: dict | None,
) -> dict:
    reasons: list[str] = []
    bundle_file_ok = False
    review_document_ok = False
    hash_binding_ok = False
    qc_ok = False
    provenance_ok = False

    if bundle is None:
        reasons.append("episode_bundle_missing")
    if review is None:
        reasons.append("release_review_package_missing")

    if bundle is not None and review is not None:
        hash_binding_ok = (
            bundle["bundle_status"] == "CURRENT"
            and review["review_status"] == "READY_FOR_HUMAN_REVIEW"
            and str(review["bundle_id"]) == str(bundle["id"])
            and job.get("episode_bundle_sha256") == bundle["bundle_sha256"]
            and job.get("release_review_package_sha256")
            == review["package_sha256"]
        )
        if not hash_binding_ok:
            reasons.append("review_identity_drift")

        try:
            path = _file_path(bundle["bundle_uri"])
            bundle_file_ok = (
                path.is_file()
                and _sha256_file(path) == bundle["bundle_sha256"]
            )
        except (ValueError, PermissionError, OSError):
            bundle_file_ok = False
        if not bundle_file_ok:
            reasons.append("episode_bundle_file_integrity_failed")

        try:
            path = _file_path(review["review_document_uri"])
            review_document_ok = (
                path.is_file()
                and _sha256_file(path)
                == review["review_document_sha256"]
            )
        except (ValueError, PermissionError, OSError):
            review_document_ok = False
        if not review_document_ok:
            reasons.append("review_document_integrity_failed")

        package_content = dict(review.get("package_content") or {})
        package_qc = dict(package_content.get("qc") or {})
        qc_ok = (
            qc is not None
            and qc["qc_status"] == "PASSED"
            and bool(qc["passed"])
            and int(qc["hard_failure_count"]) == 0
            and package_qc.get("passed") is True
            and int(package_qc.get("hard_failure_count") or -1) == 0
            and package_qc.get("report_sha256") == qc["report_sha256"]
        )
        if not qc_ok:
            reasons.append("qc_gate_not_current")

        provenance = dict(package_content.get("provenance") or {})
        assets = dict(provenance.get("assets") or {})
        voices = dict(provenance.get("voices") or {})
        provenance_ok = (
            assets.get("all_usage_rights_approved") is True
            and voices.get("all_usage_rights_approved") is True
        )
        if not provenance_ok:
            reasons.append("rights_or_provenance_not_approved")

    return {
        "allowed": not reasons,
        "hash_binding_ok": hash_binding_ok,
        "bundle_file_ok": bundle_file_ok,
        "review_document_ok": review_document_ok,
        "qc_ok": qc_ok,
        "provenance_ok": provenance_ok,
        "blocking_reasons": reasons,
    }


def list_shrimp_review_workspace(limit: int = 100) -> list[dict]:
    capped = max(1, min(int(limit), 200))
    with engine.connect() as db:
        rows = db.execute(
            text("""
              SELECT saj.provider_job_id AS job_id,
                     saj.episode_id,saj.review_status,
                     saj.episode_bundle_sha256,
                     saj.release_review_package_sha256,
                     saj.reviewed_at,saj.updated_at,
                     pj.job_status,pj.publish_enabled,
                     pj.production_execution_enabled,
                     b.byte_size AS bundle_byte_size,
                     rp.package_content,
                     d.decision,d.actor AS decision_actor,
                     d.decided_at,d.decision_status
              FROM shrimp_animation_jobs saj
              JOIN production_provider_jobs pj
                ON pj.id=saj.provider_job_id
              LEFT JOIN LATERAL (
                SELECT byte_size
                FROM shrimp_animation_episode_bundles
                WHERE provider_job_id=saj.provider_job_id
                ORDER BY created_at DESC
                LIMIT 1
              ) b ON true
              LEFT JOIN LATERAL (
                SELECT package_content
                FROM shrimp_animation_release_review_packages
                WHERE provider_job_id=saj.provider_job_id
                ORDER BY created_at DESC
                LIMIT 1
              ) rp ON true
              LEFT JOIN LATERAL (
                SELECT decision,actor,decided_at,decision_status
                FROM shrimp_animation_review_decisions
                WHERE provider_job_id=saj.provider_job_id
                ORDER BY decided_at DESC
                LIMIT 1
              ) d ON true
              WHERE saj.review_status<>'NOT_READY'
              ORDER BY saj.updated_at DESC,saj.provider_job_id DESC
              LIMIT :limit
            """),
            {"limit": capped},
        ).mappings().all()

    result = []
    for row in rows:
        item = dict(row)
        package = dict(item.pop("package_content") or {})
        episode = dict(package.get("episode") or {})
        qc = dict(package.get("qc") or {})
        item.update(
            {
                "title": episode.get("title") or item["episode_id"],
                "duration_ms": episode.get("duration_ms"),
                "width": episode.get("width"),
                "height": episode.get("height"),
                "fps": episode.get("fps"),
                "qc_passed": qc.get("passed"),
                "publish_enabled": False,
                "production_execution_enabled": False,
            }
        )
        result.append(item)
    return result


def get_shrimp_review_workspace(job_id) -> dict:
    job = _load_job(job_id)
    with engine.connect() as db:
        bundle = _latest_bundle(
            db,
            job_id,
            current_only=job["review_status"] != "STALE",
        )
        if bundle is None:
            bundle = _latest_bundle(db, job_id)
        review = _latest_review_package(
            db,
            job_id,
            current_only=job["review_status"] != "STALE",
        )
        if review is None:
            review = _latest_review_package(db, job_id)
        qc = _latest_qc(db, job_id)
        decisions = _decisions(db, job_id)

    integrity = _package_integrity(job, bundle, review, qc)
    terminal = job["review_status"] in {
        "RELEASE_APPROVED",
        "RELEASE_REJECTED",
    }
    ready = (
        job["review_status"] == "READY_FOR_HUMAN_REVIEW"
        and job["job_status"] == "QC_PASSED"
        and job["publish_enabled"] is False
        and job["production_execution_enabled"] is False
        and job["external_side_effects"] == "DENY"
    )

    package_content = (
        dict(review.get("package_content") or {})
        if review is not None
        else {}
    )
    bundle_view = None
    if bundle is not None:
        bundle_view = {
            "bundle_id": str(bundle["id"]),
            "episode_id": bundle["episode_id"],
            "packaging_version": bundle["packaging_version"],
            "sha256": bundle["bundle_sha256"],
            "byte_size": int(bundle["byte_size"]),
            "bundle_manifest_sha256": bundle["bundle_manifest_sha256"],
            "artifact_manifest": bundle["artifact_manifest"],
            "bundle_status": bundle["bundle_status"],
            "created_at": bundle["created_at"],
        }

    review_view = None
    if review is not None:
        review_view = {
            "review_package_id": str(review["id"]),
            "generator_version": review["generator_version"],
            "review_status": review["review_status"],
            "package_sha256": review["package_sha256"],
            "review_document_sha256": review["review_document_sha256"],
            "package_content": package_content,
            "created_at": review["created_at"],
        }

    return {
        "job_id": str(job["provider_job_id"]),
        "episode_id": job["episode_id"],
        "job_status": job["job_status"],
        "review_status": job["review_status"],
        "episode_bundle_sha256": job["episode_bundle_sha256"],
        "release_review_package_sha256": job[
            "release_review_package_sha256"
        ],
        "reviewed_at": job["reviewed_at"],
        "completed_at": job["completed_at"],
        "bundle": bundle_view,
        "review_package": review_view,
        "episode": dict(package_content.get("episode") or {}),
        "media": dict(package_content.get("media") or {}),
        "qc": dict(package_content.get("qc") or {}),
        "provenance": dict(package_content.get("provenance") or {}),
        "manifest_hashes": list(package_content.get("manifest_hashes") or []),
        "transcript": list(package_content.get("transcript") or []),
        "reviewer_checklist": list(
            package_content.get("reviewer_checklist") or []
        ),
        "required_checklist_keys": list(REQUIRED_REVIEW_CHECKLIST),
        "integrity_gate": integrity,
        "decisions": decisions,
        "can_approve": ready and not terminal and integrity["allowed"],
        "can_reject": ready and not terminal and bundle is not None
        and review is not None,
        "human_review_key_configured": bool(
            settings.shrimp_human_review_key.strip()
        ),
        "episode_player_url": (
            f"/v1/shrimp-animation/review-workspace/{job_id}/episode"
        ),
        "bundle_download_url": (
            f"/v1/shrimp-animation/review-workspace/{job_id}/bundle"
        ),
        "review_document_url": (
            f"/v1/shrimp-animation/review-workspace/{job_id}/review-document"
        ),
        "ui_safety": {
            "human_decision_required": True,
            "auto_publish": False,
            "publish_enabled": False,
            "production_execution_enabled": False,
            "production_deployment": False,
            "production_promotion": False,
            "production_rollback": False,
            "review_key_persisted_in_browser": False,
        },
    }


def _current_review_files(job_id) -> tuple[dict, dict, dict]:
    job = _load_job(job_id)
    if job["review_status"] == "STALE":
        raise RuntimeError("Human review artifacts are STALE")
    with engine.connect() as db:
        bundle = _latest_bundle(db, job_id, current_only=True)
        review = _latest_review_package(db, job_id, current_only=True)
    if bundle is None or review is None:
        raise RuntimeError("Current human review package is unavailable")
    if job["episode_bundle_sha256"] != bundle["bundle_sha256"]:
        raise RuntimeError("Episode Bundle identity drift detected")
    if (
        job["release_review_package_sha256"]
        != review["package_sha256"]
    ):
        raise RuntimeError("Release Review Package identity drift detected")
    return job, bundle, review


def get_episode_player_file(job_id) -> ReviewFile:
    _, bundle, review = _current_review_files(job_id)
    package = dict(review["package_content"] or {})
    media = dict(package.get("media") or {})
    episode_item = _artifact_item(package, "episode.mp4")
    if episode_item is None:
        raise RuntimeError("Episode Bundle manifest lacks episode.mp4")

    with engine.connect() as db:
        render = db.execute(
            text("""
              SELECT artifact_uri,artifact_sha256
              FROM shrimp_animation_renders
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND render_status='CURRENT'
              ORDER BY created_at DESC
              LIMIT 1
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
    if render is None:
        raise RuntimeError("Current rendered episode is unavailable")

    expected = render["artifact_sha256"]
    if expected != media.get("render_artifact_sha256"):
        raise RuntimeError("Review package media hash drift detected")
    if expected != episode_item.get("sha256"):
        raise RuntimeError("Episode Bundle media hash drift detected")
    if expected != bundle["render_artifact_sha256"]:
        raise RuntimeError("Episode Bundle render lineage drift detected")

    path = _file_path(render["artifact_uri"])
    if not path.is_file():
        raise RuntimeError("Rendered episode file is missing")
    actual = _sha256_file(path)
    if actual != expected:
        raise RuntimeError("Rendered episode bytes drift detected")
    return ReviewFile(
        path=path,
        filename=f"{bundle['episode_id']}.mp4",
        media_type="video/mp4",
        sha256=actual,
    )


def get_episode_bundle_file(job_id) -> ReviewFile:
    _, bundle, _ = _current_review_files(job_id)
    path = _file_path(bundle["bundle_uri"])
    if not path.is_file():
        raise RuntimeError("Episode Bundle file is missing")
    actual = _sha256_file(path)
    if actual != bundle["bundle_sha256"]:
        raise RuntimeError("Episode Bundle bytes drift detected")
    return ReviewFile(
        path=path,
        filename=f"{bundle['episode_id']}-bundle.zip",
        media_type="application/zip",
        sha256=actual,
    )


def get_review_document_file(job_id) -> ReviewFile:
    _, _, review = _current_review_files(job_id)
    path = _file_path(review["review_document_uri"])
    if not path.is_file():
        raise RuntimeError("Human review document is missing")
    actual = _sha256_file(path)
    if actual != review["review_document_sha256"]:
        raise RuntimeError("Human review document bytes drift detected")
    return ReviewFile(
        path=path,
        filename="episode-review.md",
        media_type="text/markdown; charset=utf-8",
        sha256=actual,
    )


def _normalize_checklist(values: list[str] | None) -> list[str]:
    normalized = sorted(
        {
            str(value).strip()
            for value in (values or [])
            if str(value).strip()
        }
    )
    unknown = sorted(
        set(normalized) - set(REQUIRED_REVIEW_CHECKLIST)
    )
    if unknown:
        raise ValueError(
            "Unknown human review checklist key(s): "
            + ", ".join(unknown)
        )
    return normalized


def decide_shrimp_release(
    job_id,
    *,
    decision: str,
    reason: str,
    actor: str,
    episode_bundle_sha256: str,
    release_review_package_sha256: str,
    confirmed_checklist: list[str] | None = None,
) -> dict:
    normalized = (decision or "").upper().strip()
    clean_reason = (reason or "").strip()
    clean_actor = (actor or "").strip()
    if normalized not in {"APPROVE", "REJECT"}:
        raise ValueError("decision must be APPROVE or REJECT")
    if len(clean_reason) < 3:
        raise ValueError("reason must contain at least 3 characters")
    if not clean_actor:
        raise ValueError("actor is required")
    if len(episode_bundle_sha256 or "") != 64:
        raise ValueError("episode_bundle_sha256 must be 64 characters")
    if len(release_review_package_sha256 or "") != 64:
        raise ValueError(
            "release_review_package_sha256 must be 64 characters"
        )
    checklist = _normalize_checklist(confirmed_checklist)
    if normalized == "APPROVE":
        missing = sorted(
            set(REQUIRED_REVIEW_CHECKLIST) - set(checklist)
        )
        if missing:
            raise PermissionError(
                "APPROVE requires all human review checklist items: "
                + ", ".join(missing)
            )

    # Re-read and hash files immediately before acquiring the terminal
    # database lock so a decision is never made against unverified bytes.
    workspace = get_shrimp_review_workspace(job_id)
    if normalized == "APPROVE" and not workspace["can_approve"]:
        reasons = workspace["integrity_gate"]["blocking_reasons"]
        raise PermissionError(
            "Human review approval gate is blocked: "
            + (", ".join(reasons) if reasons else "not review-ready")
        )
    if normalized == "REJECT" and not workspace["can_reject"]:
        raise PermissionError("Human review rejection gate is not available")
    get_episode_bundle_file(job_id)
    get_review_document_file(job_id)
    get_episode_player_file(job_id)

    with engine.begin() as db:
        row = db.execute(
            text("""
              SELECT saj.provider_job_id,saj.review_status,
                     saj.episode_bundle_sha256,
                     saj.release_review_package_sha256,
                     pj.job_status,pj.publish_enabled,
                     pj.production_execution_enabled,
                     pj.external_side_effects
              FROM shrimp_animation_jobs saj
              JOIN production_provider_jobs pj
                ON pj.id=saj.provider_job_id
              WHERE saj.provider_job_id=CAST(:job_id AS uuid)
              FOR UPDATE OF saj,pj
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
        if row is None:
            raise LookupError("Shrimp animation job not found")
        if row["review_status"] in {
            "RELEASE_APPROVED",
            "RELEASE_REJECTED",
        }:
            raise RuntimeError("Human review decision is already terminal")
        if row["review_status"] != "READY_FOR_HUMAN_REVIEW":
            raise RuntimeError("Episode is not READY_FOR_HUMAN_REVIEW")
        if row["job_status"] != "QC_PASSED":
            raise RuntimeError("Human review requires QC_PASSED")
        if row["publish_enabled"] or row["production_execution_enabled"]:
            raise RuntimeError("Human review safety invariant failed")
        if row["external_side_effects"] != "DENY":
            raise RuntimeError("Human review external side effects must be DENY")
        if row["episode_bundle_sha256"] != episode_bundle_sha256:
            raise RuntimeError("Episode Bundle SHA-256 changed during review")
        if (
            row["release_review_package_sha256"]
            != release_review_package_sha256
        ):
            raise RuntimeError(
                "Release Review Package SHA-256 changed during review"
            )

        bundle = _latest_bundle(db, job_id, current_only=True)
        review = _latest_review_package(db, job_id, current_only=True)
        qc = _latest_qc(db, job_id)
        if bundle is None or review is None or qc is None:
            raise RuntimeError("Current review evidence is incomplete")
        if bundle["bundle_sha256"] != episode_bundle_sha256:
            raise RuntimeError("Current Episode Bundle SHA-256 mismatch")
        if review["package_sha256"] != release_review_package_sha256:
            raise RuntimeError("Current Review Package SHA-256 mismatch")

        package_content = dict(review["package_content"] or {})
        package_qc = dict(package_content.get("qc") or {})
        provenance = dict(package_content.get("provenance") or {})
        assets = dict(provenance.get("assets") or {})
        voices = dict(provenance.get("voices") or {})
        if normalized == "APPROVE":
            if (
                qc["qc_status"] != "PASSED"
                or not qc["passed"]
                or int(qc["hard_failure_count"]) != 0
                or package_qc.get("passed") is not True
                or int(package_qc.get("hard_failure_count") or -1) != 0
            ):
                raise PermissionError("APPROVE requires current zero-failure QC")
            if (
                assets.get("all_usage_rights_approved") is not True
                or voices.get("all_usage_rights_approved") is not True
            ):
                raise PermissionError(
                    "APPROVE requires approved asset and voice provenance"
                )

        existing = db.execute(
            text("""
              SELECT id
              FROM shrimp_animation_review_decisions
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND decision_status='CURRENT'
              FOR UPDATE
            """),
            {"job_id": job_id},
        ).scalar_one_or_none()
        if existing is not None:
            raise RuntimeError("Current human review decision already exists")

        decision_payload = {
            "job_id": str(job_id),
            "bundle_id": str(bundle["id"]),
            "review_package_id": str(review["id"]),
            "decision": normalized,
            "reason": clean_reason,
            "actor": clean_actor,
            "episode_bundle_sha256": episode_bundle_sha256,
            "release_review_package_sha256":
                release_review_package_sha256,
            "confirmed_checklist": checklist,
        }
        decision_sha = hashlib.sha256(
            canonical_json(decision_payload).encode("utf-8")
        ).hexdigest()

        decision_id = db.execute(
            text("""
              INSERT INTO shrimp_animation_review_decisions(
                provider_job_id,bundle_id,review_package_id,
                decision,reason,actor,episode_bundle_sha256,
                release_review_package_sha256,confirmed_checklist,
                decision_sha256,decision_status)
              VALUES(
                CAST(:job_id AS uuid),CAST(:bundle_id AS uuid),
                CAST(:review_package_id AS uuid),:decision,:reason,:actor,
                :bundle_sha,:review_sha,CAST(:checklist AS jsonb),
                :decision_sha,'CURRENT')
              RETURNING id
            """),
            {
                "job_id": job_id,
                "bundle_id": bundle["id"],
                "review_package_id": review["id"],
                "decision": normalized,
                "reason": clean_reason,
                "actor": clean_actor[:200],
                "bundle_sha": episode_bundle_sha256,
                "review_sha": release_review_package_sha256,
                "checklist": canonical_json(checklist),
                "decision_sha": decision_sha,
            },
        ).scalar_one()

        new_status = (
            "RELEASE_APPROVED"
            if normalized == "APPROVE"
            else "RELEASE_REJECTED"
        )
        db.execute(
            text("""
              UPDATE shrimp_animation_jobs
              SET review_status=:status,
                  reviewed_at=now(),
                  updated_at=now()
              WHERE provider_job_id=CAST(:job_id AS uuid)
            """),
            {"job_id": job_id, "status": new_status},
        )
        db.execute(
            text("""
              INSERT INTO production_provider_events(
                job_id,stage_key,event_type,actor,payload)
              VALUES(
                CAST(:job_id AS uuid),'PACKAGE',:event_type,:actor,
                CAST(:payload AS jsonb))
            """),
            {
                "job_id": job_id,
                "event_type": (
                    "HUMAN_RELEASE_APPROVED"
                    if normalized == "APPROVE"
                    else "HUMAN_RELEASE_REJECTED"
                ),
                "actor": clean_actor[:200],
                "payload": canonical_json(
                    {
                        "decision_id": str(decision_id),
                        "decision_sha256": decision_sha,
                        "episode_bundle_sha256":
                            episode_bundle_sha256,
                        "release_review_package_sha256":
                            release_review_package_sha256,
                        "publish_enabled": False,
                        "production_execution_enabled": False,
                    }
                ),
            },
        )

    return {
        "job_id": str(job_id),
        "decision_id": str(decision_id),
        "decision": normalized,
        "decision_sha256": decision_sha,
        "review_status": new_status,
        "episode_bundle_sha256": episode_bundle_sha256,
        "release_review_package_sha256":
            release_review_package_sha256,
        "confirmed_checklist": checklist,
        "publish_enabled": False,
        "production_execution_enabled": False,
        "external_publish_performed": False,
        "production_deployment_performed": False,
        "next_stage": (
            "PUBLISHING_AUTHORIZATION"
            if normalized == "APPROVE"
            else "NONE"
        ),
    }
