from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import text

from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_accounts import (
    apply_account_defaults_and_guard,
    resolve_account_for_target,
)
from app.providers.animation.shrimp.human_review import (
    get_episode_bundle_file,
    get_episode_player_file,
    get_review_document_file,
    get_shrimp_review_workspace,
)


_TARGET_KEY = re.compile(r"^[A-Za-z0-9_.-]{3,120}$")
_ALLOWED_PLATFORMS = {"BILIBILI", "YOUTUBE", "CUSTOM"}

_DEFAULT_CONSTRAINTS = {
    "max_title_chars": 120,
    "max_description_chars": 5000,
    "max_tags": 20,
    "max_tag_chars": 60,
    "allowed_visibilities": ["DRAFT", "PRIVATE", "UNLISTED", "PUBLIC"],
    "require_category": False,
}


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def _normalize_target_key(value: str) -> str:
    result = (value or "").strip()
    if not _TARGET_KEY.fullmatch(result):
        raise ValueError(
            "target_key must contain 3-120 letters, digits, dot, dash, or underscore"
        )
    return result


def _normalize_platform(value: str) -> str:
    result = (value or "").upper().strip()
    if result not in _ALLOWED_PLATFORMS:
        raise ValueError("platform must be BILIBILI, YOUTUBE, or CUSTOM")
    return result


def _normalize_constraints(value: dict | None) -> dict:
    supplied = dict(value or {})
    unknown = sorted(set(supplied) - set(_DEFAULT_CONSTRAINTS))
    if unknown:
        raise ValueError(
            "Unknown publish metadata constraint(s): " + ", ".join(unknown)
        )
    constraints = {**_DEFAULT_CONSTRAINTS, **supplied}

    for key in (
        "max_title_chars",
        "max_description_chars",
        "max_tags",
        "max_tag_chars",
    ):
        number = int(constraints[key])
        if number < 1 or number > 20000:
            raise ValueError(f"{key} is outside local dry-run guardrails")
        constraints[key] = number

    visibilities = sorted(
        {
            str(item).upper().strip()
            for item in constraints["allowed_visibilities"]
            if str(item).strip()
        }
    )
    if not visibilities:
        raise ValueError("allowed_visibilities must not be empty")
    constraints["allowed_visibilities"] = visibilities
    constraints["require_category"] = bool(constraints["require_category"])
    return constraints


def _target_snapshot(target: dict) -> dict:
    return {
        "schema_version": "shrimp-publish-target-v0.1",
        "target_key": target["target_key"],
        "platform": target["platform"],
        "display_name": target["display_name"],
        "account_reference": target.get("account_reference"),
        "metadata_constraints": dict(target["metadata_constraints"] or {}),
        "target_status": target["target_status"],
        "execution_enabled": False,
        "external_publish_enabled": False,
    }


def _serialize_row(row: dict | Any) -> dict:
    result = dict(row)
    for key in (
        "id",
        "provider_job_id",
        "review_decision_id",
        "target_id",
        "publish_plan_id",
    ):
        if key in result and result[key] is not None:
            result[key] = str(result[key])
    return result


def register_publish_target(
    *,
    target_key: str,
    platform: str,
    display_name: str,
    account_reference: str | None = None,
    metadata_constraints: dict | None = None,
    actor: str = "shrimp-publish-target-api",
) -> dict:
    key = _normalize_target_key(target_key)
    normalized_platform = _normalize_platform(platform)
    name = (display_name or "").strip()
    if not name:
        raise ValueError("display_name is required")
    account = (
        (account_reference or "").strip() or None
    )
    constraints = _normalize_constraints(metadata_constraints)
    clean_actor = (actor or "").strip() or "shrimp-publish-target-api"

    material = {
        "target_key": key,
        "platform": normalized_platform,
        "display_name": name,
        "account_reference": account,
        "metadata_constraints": constraints,
        "target_status": "ACTIVE",
        "execution_enabled": False,
        "external_publish_enabled": False,
    }

    with engine.begin() as db:
        existing = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_targets
              WHERE target_key=:target_key
              FOR UPDATE
            """),
            {"target_key": key},
        ).mappings().one_or_none()
        if existing is not None:
            current = _target_snapshot(dict(existing))
            expected = {
                "schema_version": "shrimp-publish-target-v0.1",
                **material,
            }
            if current != expected:
                raise RuntimeError(
                    "Publish Target already exists with a different immutable Step 9 definition"
                )
            return _serialize_row(existing)

        row = db.execute(
            text("""
              INSERT INTO shrimp_animation_publish_targets(
                target_key,platform,display_name,account_reference,
                metadata_constraints,target_status,execution_enabled,
                external_publish_enabled,created_by)
              VALUES(
                :target_key,:platform,:display_name,:account_reference,
                CAST(:constraints AS jsonb),'ACTIVE',false,false,:actor)
              RETURNING *
            """),
            {
                "target_key": key,
                "platform": normalized_platform,
                "display_name": name,
                "account_reference": account,
                "constraints": canonical_json(constraints),
                "actor": clean_actor[:200],
            },
        ).mappings().one()
    return _serialize_row(row)


def list_publish_targets(*, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM shrimp_animation_publish_targets"
    if active_only:
        sql += " WHERE target_status='ACTIVE'"
    sql += " ORDER BY platform,target_key"
    with engine.connect() as db:
        rows = db.execute(text(sql)).mappings().all()
    return [_serialize_row(row) for row in rows]


def get_publish_target(target_key: str) -> dict:
    key = _normalize_target_key(target_key)
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_targets
              WHERE target_key=:target_key
            """),
            {"target_key": key},
        ).mappings().one_or_none()
    if row is None:
        raise LookupError("Publish Target not found")
    return _serialize_row(row)


def _normalize_publish_metadata(
    metadata: dict,
    constraints: dict,
) -> dict:
    value = dict(metadata or {})
    allowed = {
        "title",
        "description",
        "tags",
        "category",
        "visibility",
        "cover_artifact_sha256",
        "scheduled_for",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            "Unknown publish metadata field(s): " + ", ".join(unknown)
        )

    title = str(value.get("title") or "").strip()
    if not title:
        raise ValueError("publish metadata title is required")
    if len(title) > int(constraints["max_title_chars"]):
        raise ValueError("publish metadata title exceeds local target guardrail")

    description = str(value.get("description") or "").strip()
    if len(description) > int(constraints["max_description_chars"]):
        raise ValueError(
            "publish metadata description exceeds local target guardrail"
        )

    raw_tags = value.get("tags") or []
    if not isinstance(raw_tags, list):
        raise ValueError("publish metadata tags must be a list")
    tags = []
    for raw in raw_tags:
        tag = str(raw).strip()
        if not tag:
            continue
        if len(tag) > int(constraints["max_tag_chars"]):
            raise ValueError("publish metadata tag exceeds local target guardrail")
        if tag not in tags:
            tags.append(tag)
    if len(tags) > int(constraints["max_tags"]):
        raise ValueError("publish metadata tags exceed local target guardrail")

    category = str(value.get("category") or "").strip() or None
    if constraints.get("require_category") and not category:
        raise ValueError("publish metadata category is required by target")

    visibility = str(value.get("visibility") or "DRAFT").upper().strip()
    if visibility not in set(constraints["allowed_visibilities"]):
        raise ValueError("publish metadata visibility is not allowed by target")

    cover_sha = str(value.get("cover_artifact_sha256") or "").strip() or None
    if cover_sha is not None and len(cover_sha) != 64:
        raise ValueError("cover_artifact_sha256 must be a 64-character SHA-256")

    scheduled_for = str(value.get("scheduled_for") or "").strip() or None

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "category": category,
        "visibility": visibility,
        "cover_artifact_sha256": cover_sha,
        "scheduled_for": scheduled_for,
    }


def _current_approved_review(db, job_id) -> dict:
    row = db.execute(
        text("""
          SELECT saj.provider_job_id,saj.episode_id,saj.review_status,
                 saj.episode_bundle_sha256,
                 saj.release_review_package_sha256,
                 pj.job_status,pj.publish_enabled,
                 pj.production_execution_enabled,pj.external_side_effects,
                 rd.id AS review_decision_id,
                 rd.decision AS review_decision,
                 rd.decision_sha256 AS review_decision_sha256,
                 rd.decision_status AS review_decision_status,
                 rd.episode_bundle_sha256 AS decision_bundle_sha256,
                 rd.release_review_package_sha256 AS decision_review_sha256
          FROM shrimp_animation_jobs saj
          JOIN production_provider_jobs pj
            ON pj.id=saj.provider_job_id
          JOIN LATERAL (
            SELECT *
            FROM shrimp_animation_review_decisions
            WHERE provider_job_id=saj.provider_job_id
              AND decision_status='CURRENT'
            ORDER BY decided_at DESC,id DESC
            LIMIT 1
          ) rd ON true
          WHERE saj.provider_job_id=CAST(:job_id AS uuid)
        """),
        {"job_id": job_id},
    ).mappings().one_or_none()
    if row is None:
        raise LookupError("Current approved Shrimp human review decision not found")
    result = dict(row)
    if result["review_status"] != "RELEASE_APPROVED":
        raise RuntimeError("Publisher Plan requires RELEASE_APPROVED")
    if (
        result["review_decision"] != "APPROVE"
        or result["review_decision_status"] != "CURRENT"
    ):
        raise RuntimeError("Publisher Plan requires current APPROVE decision")
    if result["job_status"] != "QC_PASSED":
        raise RuntimeError("Publisher Plan requires QC_PASSED")
    if result["publish_enabled"] or result["production_execution_enabled"]:
        raise RuntimeError("Publisher Plan safety invariant failed")
    if result["external_side_effects"] != "DENY":
        raise RuntimeError("Publisher Plan requires external_side_effects=DENY")
    if (
        result["episode_bundle_sha256"]
        != result["decision_bundle_sha256"]
        or result["release_review_package_sha256"]
        != result["decision_review_sha256"]
    ):
        raise RuntimeError("Step 8 review decision hash binding drifted")
    return result


def _build_dry_run(
    *,
    source: dict,
    target: dict,
    metadata: dict,
) -> dict:
    constraints = dict(target["metadata_constraints"] or {})
    normalized_metadata = _normalize_publish_metadata(metadata, constraints)
    target_snapshot = _target_snapshot(target)
    target_sha = _sha256(target_snapshot)

    checks = [
        {"key": "release_approved", "passed": True},
        {"key": "review_decision_current", "passed": True},
        {"key": "episode_bundle_hash_bound", "passed": True},
        {"key": "review_package_hash_bound", "passed": True},
        {"key": "target_active", "passed": target["target_status"] == "ACTIVE"},
        {
            "key": "target_execution_disabled",
            "passed": target["execution_enabled"] is False,
        },
        {
            "key": "external_publish_disabled",
            "passed": target["external_publish_enabled"] is False,
        },
        {"key": "metadata_contract_valid", "passed": True},
        {"key": "network_request_count_zero", "passed": True},
        {"key": "credential_access_count_zero", "passed": True},
        {"key": "external_write_count_zero", "passed": True},
    ]
    if not all(item["passed"] for item in checks):
        failed = [item["key"] for item in checks if not item["passed"]]
        raise RuntimeError("Publisher dry-run failed: " + ", ".join(failed))

    snapshot = {
        "schema_version": "shrimp-publisher-dry-run-v0.1",
        "provider_job_id": str(source["provider_job_id"]),
        "episode_id": source["episode_id"],
        "platform": target["platform"],
        "target_key": target["target_key"],
        "review_decision_sha256": source["review_decision_sha256"],
        "episode_bundle_sha256": source["episode_bundle_sha256"],
        "release_review_package_sha256":
            source["release_review_package_sha256"],
        "target_snapshot_sha256": target_sha,
        "publish_metadata": normalized_metadata,
        "checks": checks,
        "network_request_count": 0,
        "credential_access_count": 0,
        "external_write_count": 0,
        "execution_enabled": False,
        "external_publish_enabled": False,
        "publish_performed": False,
        "external_side_effects": "DENY",
    }
    return {
        "publish_metadata": normalized_metadata,
        "target_snapshot": target_snapshot,
        "target_snapshot_sha256": target_sha,
        "dry_run_snapshot": snapshot,
        "dry_run_sha256": _sha256(snapshot),
    }


def _verify_step8_files(job_id) -> dict:
    workspace = get_shrimp_review_workspace(job_id)
    if workspace["review_status"] != "RELEASE_APPROVED":
        raise RuntimeError("Step 8 review is not RELEASE_APPROVED")
    if workspace["integrity_gate"]["allowed"] is not True:
        reasons = workspace["integrity_gate"]["blocking_reasons"]
        raise RuntimeError(
            "Publisher Plan blocked by Step 8 integrity: "
            + ", ".join(reasons)
        )
    bundle = get_episode_bundle_file(job_id)
    review = get_review_document_file(job_id)
    episode = get_episode_player_file(job_id)
    return {
        "episode_bundle_sha256": bundle.sha256,
        "review_document_sha256": review.sha256,
        "episode_media_sha256": episode.sha256,
    }


def create_publish_plan(
    job_id,
    *,
    target_key: str,
    publish_metadata: dict,
    actor: str = "shrimp-publish-plan-api",
) -> dict:
    key = _normalize_target_key(target_key)
    clean_actor = (actor or "").strip() or "shrimp-publish-plan-api"
    file_integrity = _verify_step8_files(job_id)

    with engine.begin() as db:
        source = _current_approved_review(db, job_id)
        target = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_targets
              WHERE target_key=:target_key
              FOR UPDATE
            """),
            {"target_key": key},
        ).mappings().one_or_none()
        if target is None:
            raise LookupError("Publish Target not found")
        target = dict(target)
        if target["target_status"] != "ACTIVE":
            raise RuntimeError("Publish Target is not ACTIVE")
        if target["execution_enabled"] or target["external_publish_enabled"]:
            raise RuntimeError("Publish Target execution must remain disabled")

        account_profile = resolve_account_for_target(db, target)
        account_snapshot = None
        account_profile_sha256 = None
        effective_metadata = dict(publish_metadata or {})
        if target["platform"] == "BILIBILI" and account_profile is not None:
            (
                effective_metadata,
                account_snapshot,
                account_profile_sha256,
            ) = apply_account_defaults_and_guard(
                db,
                account_profile,
                effective_metadata,
            )

        dry_run = _build_dry_run(
            source=source,
            target=target,
            metadata=effective_metadata,
        )
        if file_integrity["episode_bundle_sha256"] != source[
            "episode_bundle_sha256"
        ]:
            raise RuntimeError("Episode Bundle bytes drifted before Publisher Plan")

        plan_payload = {
            "schema_version": "shrimp-publisher-plan-v0.1",
            "provider_job_id": str(source["provider_job_id"]),
            "episode_id": source["episode_id"],
            "review_decision_id": str(source["review_decision_id"]),
            "review_decision_sha256": source["review_decision_sha256"],
            "episode_bundle_sha256": source["episode_bundle_sha256"],
            "release_review_package_sha256":
                source["release_review_package_sha256"],
            "platform": target["platform"],
            "target_key": target["target_key"],
            "target_snapshot_sha256": dry_run["target_snapshot_sha256"],
            "account_profile_id":
                str(account_profile["id"]) if account_profile else None,
            "account_profile_sha256": account_profile_sha256,
            "account_profile_snapshot": account_snapshot,
            "publish_metadata": dry_run["publish_metadata"],
            "dry_run_sha256": dry_run["dry_run_sha256"],
            "dry_run_status": "VERIFIED",
            "execution_enabled": False,
            "external_publish_enabled": False,
            "publish_performed": False,
            "external_side_effects": "DENY",
        }
        plan_sha = _sha256(plan_payload)

        existing = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_plans
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND target_id=:target_id
                AND plan_status<>'STALE'
              FOR UPDATE
            """),
            {"job_id": job_id, "target_id": target["id"]},
        ).mappings().one_or_none()
        if existing is not None:
            if existing["plan_sha256"] != plan_sha:
                raise RuntimeError(
                    "Current Publisher Plan already exists with different immutable metadata"
                )
            result = _serialize_row(existing)
            result["replayed"] = True
            return result

        row = db.execute(
            text("""
              INSERT INTO shrimp_animation_publish_plans(
                provider_job_id,review_decision_id,target_id,
                platform,target_key,review_decision_sha256,
                episode_bundle_sha256,release_review_package_sha256,
                target_snapshot_sha256,account_profile_id,
                account_profile_sha256,publish_metadata,
                dry_run_snapshot,dry_run_sha256,dry_run_status,
                plan_payload,plan_sha256,plan_status,
                execution_enabled,publish_performed,created_by)
              VALUES(
                CAST(:job_id AS uuid),:review_decision_id,:target_id,
                :platform,:target_key,:review_decision_sha256,
                :episode_bundle_sha256,:release_review_package_sha256,
                :target_snapshot_sha256,:account_profile_id,
                :account_profile_sha256,CAST(:publish_metadata AS jsonb),
                CAST(:dry_run_snapshot AS jsonb),:dry_run_sha256,'VERIFIED',
                CAST(:plan_payload AS jsonb),:plan_sha256,
                'PENDING_AUTHORIZATION',false,false,:actor)
              RETURNING *
            """),
            {
                "job_id": job_id,
                "review_decision_id": source["review_decision_id"],
                "target_id": target["id"],
                "platform": target["platform"],
                "target_key": target["target_key"],
                "review_decision_sha256": source["review_decision_sha256"],
                "episode_bundle_sha256": source["episode_bundle_sha256"],
                "release_review_package_sha256":
                    source["release_review_package_sha256"],
                "target_snapshot_sha256": dry_run["target_snapshot_sha256"],
                "account_profile_id":
                    account_profile["id"] if account_profile else None,
                "account_profile_sha256": account_profile_sha256,
                "publish_metadata": canonical_json(
                    dry_run["publish_metadata"]
                ),
                "dry_run_snapshot": canonical_json(
                    dry_run["dry_run_snapshot"]
                ),
                "dry_run_sha256": dry_run["dry_run_sha256"],
                "plan_payload": canonical_json(plan_payload),
                "plan_sha256": plan_sha,
                "actor": clean_actor[:200],
            },
        ).mappings().one()

    result = _serialize_row(row)
    result.update(
        {
            "replayed": False,
            "dry_run_verified": True,
            "network_request_count": 0,
            "credential_access_count": 0,
            "external_write_count": 0,
            "execution_enabled": False,
            "publish_performed": False,
            "next_stage": "PUBLISH_AUTHORIZATION",
        }
    )
    return result


def _current_authorization_snapshot(db, plan_id) -> dict | None:
    row = db.execute(
        text("""
          SELECT pp.*,
                 pt.display_name AS current_target_display_name,
                 pt.account_reference AS current_account_reference,
                 pt.metadata_constraints AS current_metadata_constraints,
                 pt.target_status AS current_target_status,
                 pt.execution_enabled AS target_execution_enabled,
                 pt.external_publish_enabled AS target_external_publish_enabled,
                 saj.review_status AS current_review_status,
                 saj.episode_bundle_sha256 AS current_bundle_sha256,
                 saj.release_review_package_sha256 AS current_review_sha256,
                 pj.job_status AS current_job_status,
                 pj.publish_enabled AS job_publish_enabled,
                 pj.production_execution_enabled AS job_execution_enabled,
                 pj.external_side_effects AS current_external_side_effects,
                 rd.decision AS current_review_decision,
                 rd.decision_sha256 AS current_review_decision_sha256,
                 rd.decision_status AS current_review_decision_status
          FROM shrimp_animation_publish_plans pp
          JOIN shrimp_animation_publish_targets pt ON pt.id=pp.target_id
          JOIN shrimp_animation_jobs saj
            ON saj.provider_job_id=pp.provider_job_id
          JOIN production_provider_jobs pj
            ON pj.id=pp.provider_job_id
          JOIN shrimp_animation_review_decisions rd
            ON rd.id=pp.review_decision_id
          WHERE pp.id=CAST(:plan_id AS uuid)
          FOR UPDATE OF pp,pt,saj,pj,rd
        """),
        {"plan_id": plan_id},
    ).mappings().one_or_none()
    return dict(row) if row else None


def _authorization_drift_reasons(row: dict) -> tuple[list[str], dict, str]:
    target = {
        "target_key": row["target_key"],
        "platform": row["platform"],
        "display_name": row["current_target_display_name"],
        "account_reference": row["current_account_reference"],
        "metadata_constraints": dict(row["current_metadata_constraints"] or {}),
        "target_status": row["current_target_status"],
        "execution_enabled": row["target_execution_enabled"],
        "external_publish_enabled": row["target_external_publish_enabled"],
    }
    target_sha = _sha256(_target_snapshot(target))

    source = {
        "provider_job_id": row["provider_job_id"],
        "episode_id": row["plan_payload"]["episode_id"],
        "review_decision_sha256": row["current_review_decision_sha256"],
        "episode_bundle_sha256": row["current_bundle_sha256"],
        "release_review_package_sha256": row["current_review_sha256"],
    }
    current_dry = _build_dry_run(
        source=source,
        target=target,
        metadata=dict(row["publish_metadata"] or {}),
    )
    reasons: list[str] = []

    if row["plan_status"] != "PENDING_AUTHORIZATION":
        reasons.append("publish_plan_not_pending")
    if row["dry_run_status"] != "VERIFIED":
        reasons.append("dry_run_not_verified")
    if row["execution_enabled"] or row["publish_performed"]:
        reasons.append("publisher_execution_enabled_unexpectedly")
    if row["current_target_status"] != "ACTIVE":
        reasons.append("publish_target_not_active")
    if row["target_execution_enabled"] or row["target_external_publish_enabled"]:
        reasons.append("publish_target_execution_enabled_unexpectedly")
    if row["current_review_status"] != "RELEASE_APPROVED":
        reasons.append("release_not_approved")
    if row["current_job_status"] != "QC_PASSED":
        reasons.append("job_not_qc_passed")
    if row["job_publish_enabled"] or row["job_execution_enabled"]:
        reasons.append("job_execution_enabled_unexpectedly")
    if row["current_external_side_effects"] != "DENY":
        reasons.append("external_side_effects_not_denied")
    if (
        row["current_review_decision"] != "APPROVE"
        or row["current_review_decision_status"] != "CURRENT"
    ):
        reasons.append("human_review_decision_not_current_approve")
    if (
        row["current_review_decision_sha256"]
        != row["review_decision_sha256"]
    ):
        reasons.append("review_decision_sha256_drift")
    if row["current_bundle_sha256"] != row["episode_bundle_sha256"]:
        reasons.append("episode_bundle_sha256_drift")
    if (
        row["current_review_sha256"]
        != row["release_review_package_sha256"]
    ):
        reasons.append("release_review_package_sha256_drift")
    if target_sha != row["target_snapshot_sha256"]:
        reasons.append("publish_target_snapshot_drift")
    if current_dry["dry_run_sha256"] != row["dry_run_sha256"]:
        reasons.append("dry_run_sha256_drift")

    return list(dict.fromkeys(reasons)), current_dry, target_sha


def decide_publish_authorization(
    plan_id,
    *,
    decision: str,
    reason: str,
    actor: str,
    plan_sha256: str,
    dry_run_sha256: str,
) -> dict:
    normalized = (decision or "").upper().strip()
    clean_reason = (reason or "").strip()
    clean_actor = (actor or "").strip() or "shrimp-publish-auth-api"
    supplied_plan_sha = (plan_sha256 or "").lower().strip()
    supplied_dry_sha = (dry_run_sha256 or "").lower().strip()

    if normalized not in {"AUTHORIZE", "REJECT"}:
        raise ValueError("decision must be AUTHORIZE or REJECT")
    if len(clean_reason) < 3:
        raise ValueError("reason must contain at least 3 characters")
    if len(supplied_plan_sha) != 64:
        raise ValueError("plan_sha256 must be a 64-character SHA-256")
    if len(supplied_dry_sha) != 64:
        raise ValueError("dry_run_sha256 must be a 64-character SHA-256")

    if normalized == "AUTHORIZE":
        # Re-hash all Step 8 review files immediately before the DB decision.
        # No publisher credential or external network is used in Step 9.
        with engine.connect() as pre_db:
            pre = pre_db.execute(
                text("""
                  SELECT provider_job_id
                  FROM shrimp_animation_publish_plans
                  WHERE id=CAST(:plan_id AS uuid)
                """),
                {"plan_id": plan_id},
            ).mappings().one_or_none()
        if pre is None:
            raise LookupError("Publisher Plan not found")
        _verify_step8_files(pre["provider_job_id"])

    blocked_error = None
    block_id = None
    decision_id = None
    final_status = None
    decision_sha = None

    with engine.begin() as db:
        row = _current_authorization_snapshot(db, plan_id)
        if row is None:
            raise LookupError("Publisher Plan not found")
        if row["plan_status"] != "PENDING_AUTHORIZATION":
            raise RuntimeError("Publish authorization decision is already terminal")
        if supplied_plan_sha != str(row["plan_sha256"]).lower():
            raise RuntimeError("Publisher Plan SHA-256 does not match")
        if supplied_dry_sha != str(row["dry_run_sha256"]).lower():
            raise RuntimeError("Publisher dry-run SHA-256 does not match")
        if row["execution_enabled"] or row["publish_performed"]:
            raise RuntimeError("Publisher execution must remain disabled")

        if normalized == "AUTHORIZE":
            reasons, current_dry, current_target_sha = (
                _authorization_drift_reasons(row)
            )
            if reasons:
                message = (
                    "Publish Authorization blocked: " + ", ".join(reasons)
                )
                block_id = db.execute(
                    text("""
                      INSERT INTO shrimp_animation_publish_authorization_blocks(
                        publish_plan_id,actor,reason,blocking_reasons,
                        current_review_decision_sha256,
                        current_target_snapshot_sha256,
                        current_dry_run_sha256)
                      VALUES(
                        :plan_id,:actor,:reason,
                        CAST(:blocking_reasons AS jsonb),
                        :review_sha,:target_sha,:dry_sha)
                      RETURNING id
                    """),
                    {
                        "plan_id": row["id"],
                        "actor": clean_actor[:200],
                        "reason": message[:4000],
                        "blocking_reasons": json.dumps(
                            reasons,
                            ensure_ascii=False,
                        ),
                        "review_sha": row[
                            "current_review_decision_sha256"
                        ],
                        "target_sha": current_target_sha,
                        "dry_sha": current_dry["dry_run_sha256"],
                    },
                ).scalar_one()
                blocked_error = message
            else:
                final_status = "PUBLISH_AUTHORIZED"
        else:
            final_status = "PUBLISH_REJECTED"

        if blocked_error is None:
            decision_material = {
                "schema_version":
                    "shrimp-publish-authorization-decision-v0.1",
                "publish_plan_id": str(row["id"]),
                "decision": normalized,
                "reason": clean_reason,
                "actor": clean_actor[:200],
                "plan_sha256": row["plan_sha256"],
                "dry_run_sha256": row["dry_run_sha256"],
            }
            decision_sha = _sha256(decision_material)
            decision_id = db.execute(
                text("""
                  INSERT INTO shrimp_animation_publish_authorization_decisions(
                    publish_plan_id,decision,reason,actor,
                    plan_sha256,dry_run_sha256,decision_sha256,
                    decision_status)
                  VALUES(
                    :plan_id,:decision,:reason,:actor,
                    :plan_sha,:dry_sha,:decision_sha,'CURRENT')
                  RETURNING id
                """),
                {
                    "plan_id": row["id"],
                    "decision": normalized,
                    "reason": clean_reason[:4000],
                    "actor": clean_actor[:200],
                    "plan_sha": row["plan_sha256"],
                    "dry_sha": row["dry_run_sha256"],
                    "decision_sha": decision_sha,
                },
            ).scalar_one()

            if normalized == "AUTHORIZE":
                db.execute(
                    text("""
                      UPDATE shrimp_animation_publish_plans
                      SET plan_status='PUBLISH_AUTHORIZED',
                          authorized_at=now(),
                          execution_enabled=false,
                          publish_performed=false
                      WHERE id=:id
                    """),
                    {"id": row["id"]},
                )
            else:
                db.execute(
                    text("""
                      UPDATE shrimp_animation_publish_plans
                      SET plan_status='PUBLISH_REJECTED',
                          rejected_at=now(),
                          execution_enabled=false,
                          publish_performed=false
                      WHERE id=:id
                    """),
                    {"id": row["id"]},
                )

    if blocked_error is not None:
        raise RuntimeError(
            blocked_error + f" [block_event_id={block_id}]"
        )

    return {
        "publish_plan_id": str(plan_id),
        "decision_id": str(decision_id),
        "decision": normalized,
        "decision_sha256": decision_sha,
        "plan_status": final_status,
        "plan_sha256": supplied_plan_sha,
        "dry_run_sha256": supplied_dry_sha,
        "execution_enabled": False,
        "publish_performed": False,
        "external_publish_performed": False,
        "network_request_count": 0,
        "credential_access_count": 0,
        "external_write_count": 0,
        "next_stage": (
            "CONTROLLED_PUBLISHER_EXECUTION"
            if normalized == "AUTHORIZE"
            else "NONE"
        ),
    }


def get_publish_plan(plan_id) -> dict:
    with engine.connect() as db:
        plan = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_plans
              WHERE id=CAST(:plan_id AS uuid)
            """),
            {"plan_id": plan_id},
        ).mappings().one_or_none()
        if plan is None:
            raise LookupError("Publisher Plan not found")
        decisions = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_authorization_decisions
              WHERE publish_plan_id=:plan_id
              ORDER BY decided_at DESC,id DESC
            """),
            {"plan_id": plan["id"]},
        ).mappings().all()
        blocks = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_publish_authorization_blocks
              WHERE publish_plan_id=:plan_id
              ORDER BY blocked_at DESC,id DESC
            """),
            {"plan_id": plan["id"]},
        ).mappings().all()
    result = _serialize_row(plan)
    result["decisions"] = [_serialize_row(row) for row in decisions]
    result["blocks"] = [_serialize_row(row) for row in blocks]
    return result


def list_publish_plans(job_id) -> list[dict]:
    with engine.connect() as db:
        rows = db.execute(
            text("""
              SELECT pp.*,pt.display_name AS target_display_name
              FROM shrimp_animation_publish_plans pp
              JOIN shrimp_animation_publish_targets pt ON pt.id=pp.target_id
              WHERE pp.provider_job_id=CAST(:job_id AS uuid)
              ORDER BY pp.created_at DESC,pp.id DESC
            """),
            {"job_id": job_id},
        ).mappings().all()
    return [_serialize_row(row) for row in rows]
