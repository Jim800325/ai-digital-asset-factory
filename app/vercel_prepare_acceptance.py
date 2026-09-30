from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import database_selection, engine
from app.deployment_authorization import (
    create_deployment_plan,
    decide_deployment_authorization,
)
from app.live_acceptance_registry import list_live_acceptance_audits
from app.migrate import migration_status
from app.production_execution_adapter import (
    REAL_PRODUCTION_PROJECT_ID,
    VercelControlledExecutionAdapter,
)
from app.production_release import (
    create_production_release_execution,
    decide_production_execution,
    get_production_release_execution,
    prepare_vercel_candidate,
    reconcile_vercel_prepare,
)
from app.release_gate import decide_release_candidate, ensure_release_candidate


_NAMESPACE = uuid.UUID("8e0c78d6-4665-4d8d-a678-8fd07ec8f1ef")


class VercelPrepareAcceptanceError(RuntimeError):
    pass


def _sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _source_commit() -> str:
    value = (os.getenv("VERCEL_GIT_COMMIT_SHA") or "").strip().lower()
    if (
        len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise VercelPrepareAcceptanceError(
            "Step 4A requires a full VERCEL_GIT_COMMIT_SHA"
        )
    return value


def _preview_runtime() -> dict[str, Any]:
    if (os.getenv("VERCEL_ENV") or "").strip().lower() != "preview":
        raise VercelPrepareAcceptanceError(
            "Step 4A acceptance is available only in Vercel Preview"
        )
    if settings.production_execution_preview_only is not True:
        raise VercelPrepareAcceptanceError(
            "Step 4A requires PRODUCTION_EXECUTION_PREVIEW_ONLY=true"
        )

    selected = database_selection()
    if (
        selected.get("source") != "PREVIEW_DATABASE_URL"
        or selected.get("preview_isolated") is not True
    ):
        raise VercelPrepareAcceptanceError(
            "Step 4A requires the isolated PREVIEW_DATABASE_URL"
        )
    return selected


def _single_target() -> tuple[str, str]:
    projects = settings.production_execution_allowed_project_id_list
    teams = settings.production_execution_allowed_team_id_list
    if len(projects) != 1:
        raise VercelPrepareAcceptanceError(
            "Step 4A requires exactly one sacrificial project in the allowlist"
        )
    if len(teams) != 1:
        raise VercelPrepareAcceptanceError(
            "Step 4A requires exactly one sacrificial team in the allowlist"
        )
    project_id = projects[0]
    team_id = teams[0]
    if project_id == REAL_PRODUCTION_PROJECT_ID:
        raise VercelPrepareAcceptanceError(
            "The real Production project cannot be the sacrificial target"
        )
    return project_id, team_id


def _latest_verified_audit() -> dict[str, Any]:
    for item in list_live_acceptance_audits(50):
        if (
            item.get("acceptance_status") == "PASSED"
            and item.get("integrity_status") == "VERIFIED"
            and item.get("live_model_verified") is True
            and item.get("tests_passed") is True
            and item.get("budget_status") == "WITHIN_BUDGET"
            and item.get("external_side_effects") == "DENY"
            and item.get("source_tree_sha256")
        ):
            return item
    raise VercelPrepareAcceptanceError(
        "No VERIFIED controlled Live Acceptance audit is available"
    )


def _source_fixture(audit_id: str) -> dict[str, Any] | None:
    with engine.connect() as db:
        row = db.execute(text("""
          SELECT bp.id AS proposal_id,
                 bp.revision AS proposal_revision,
                 bp.source_fingerprint,
                 sbr.id AS request_id,
                 sr.id AS run_id,
                 oe.id AS openhands_execution_id,
                 oe.cli_version,
                 oe.model_name,
                 oe.inner_runtime,
                 oe.network_policy,
                 oe.gateway_mode,
                 oe.task_sha256,
                 oe.exit_code AS openhands_exit_code,
                 oe.trace_jsonl,
                 oe.budget_status,
                 oe.gateway_request_count,
                 oe.prompt_tokens,
                 oe.completion_tokens,
                 oe.total_tokens,
                 oe.estimated_cost_usd,
                 oe.budget_snapshot,
                 oe.live_model_verified
          FROM build_proposals bp
          JOIN sandbox_build_requests sbr
            ON sbr.proposal_id=bp.id
           AND sbr.proposal_revision=bp.revision
          JOIN sandbox_runs sr ON sr.request_id=sbr.id
          JOIN openhands_executions oe
            ON oe.request_id=sbr.id
           AND oe.run_id=sr.id
          WHERE bp.generator_version='preview-live-acceptance-bridge-v1'
            AND bp.source_snapshot->>'audit_id'=:audit_id
            AND bp.proposal_status='APPROVED'
            AND bp.execution_enabled=false
            AND sbr.request_status='ARTIFACT_READY'
            AND oe.gateway_mode='PROXY'
            AND oe.budget_status='WITHIN_BUDGET'
            AND oe.live_model_verified=true
            AND COALESCE(oe.exit_code,1)=0
          ORDER BY sbr.finished_at DESC NULLS LAST,sbr.id
          LIMIT 1
        """), {"audit_id": audit_id}).mappings().one_or_none()
    return dict(row) if row is not None else None


def _read_source_artifacts(run_id) -> list[dict[str, Any]]:
    with engine.connect() as db:
        rows = db.execute(text("""
          SELECT sa.relative_path,sa.sha256,sa.byte_size,sa.media_type,
                 sac.content_bytes,sac.content_sha256
          FROM sandbox_artifacts sa
          JOIN sandbox_artifact_contents sac
            ON sac.artifact_id=sa.id
          WHERE sa.run_id=:run_id
          ORDER BY sa.relative_path
        """), {"run_id": run_id}).mappings().all()
    result = [dict(row) for row in rows]
    if not result:
        raise VercelPrepareAcceptanceError(
            "Preview source fixture contains no persisted artifact bytes"
        )
    for item in result:
        raw = bytes(item["content_bytes"])
        actual = hashlib.sha256(raw).hexdigest()
        if (
            actual != item["sha256"]
            or actual != item["content_sha256"]
            or len(raw) != int(item["byte_size"])
        ):
            raise VercelPrepareAcceptanceError(
                "Preview source fixture artifact bytes failed SHA-256 verification"
            )
    return result


def _read_source_test(run_id) -> dict[str, Any]:
    with engine.connect() as db:
        row = db.execute(text("""
          SELECT test_command,exit_code,stdout,stderr,passed
          FROM sandbox_test_results
          WHERE run_id=:run_id
            AND passed=true
          ORDER BY captured_at DESC,id DESC
          LIMIT 1
        """), {"run_id": run_id}).mappings().one_or_none()
    if row is None:
        raise VercelPrepareAcceptanceError(
            "Preview source fixture contains no passed test result"
        )
    return dict(row)


def _acceptance_id(
    *,
    source_commit: str,
    audit_id: str,
    project_id: str,
    team_id: str,
) -> uuid.UUID:
    return uuid.uuid5(
        _NAMESPACE,
        "|".join((source_commit, audit_id, project_id, team_id)),
    )


def _child_id(run_id: uuid.UUID, kind: str, value: str = "") -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{run_id}|{kind}|{value}")


def _configured_adapter() -> VercelControlledExecutionAdapter:
    if settings.production_execution_adapter.strip().upper() != (
        "VERCEL_CONTROLLED_EXECUTOR"
    ):
        raise VercelPrepareAcceptanceError(
            "Step 4A requires PRODUCTION_EXECUTION_ADAPTER=VERCEL_CONTROLLED_EXECUTOR"
        )
    if settings.controlled_production_executor_enabled is not True:
        raise VercelPrepareAcceptanceError(
            "Step 4A requires CONTROLLED_PRODUCTION_EXECUTOR_ENABLED=true"
        )
    if settings.production_promotion_enabled:
        raise VercelPrepareAcceptanceError(
            "Step 4A requires PRODUCTION_PROMOTION_ENABLED=false"
        )
    if settings.production_rollback_enabled:
        raise VercelPrepareAcceptanceError(
            "Step 4A requires PRODUCTION_ROLLBACK_ENABLED=false"
        )
    if not settings.vercel_controlled_executor_token.strip():
        raise VercelPrepareAcceptanceError(
            "VERCEL_CONTROLLED_EXECUTOR_TOKEN is not configured"
        )
    return VercelControlledExecutionAdapter()


def _denylist_probe(
    adapter: VercelControlledExecutionAdapter,
    *,
    team_id: str,
) -> bool:
    try:
        adapter.validate_target({
            "target_provider": "VERCEL",
            "target_environment": "production",
            "target_project_id": REAL_PRODUCTION_PROJECT_ID,
            "target_team_id": team_id,
        })
    except RuntimeError as exc:
        return "denylisted" in str(exc).lower()
    return False


def acceptance_readiness() -> dict[str, Any]:
    reasons: list[str] = []
    selected: dict[str, Any] = {}
    try:
        selected = _preview_runtime()
    except Exception as exc:
        reasons.append(str(exc))

    migrations: dict[str, Any]
    try:
        migrations = migration_status()
        if (
            migrations.get("status") != "CURRENT"
            or migrations.get("latest_version")
            != "027_vercel_prepare_live_acceptance.sql"
        ):
            reasons.append("migration_027_not_current")
    except Exception as exc:
        migrations = {"status": "ERROR", "detail": str(exc)}
        reasons.append("migration_status_unavailable")

    try:
        source_commit = _source_commit()
    except Exception as exc:
        source_commit = None
        reasons.append(str(exc))

    try:
        project_id, team_id = _single_target()
    except Exception as exc:
        project_id = None
        team_id = None
        reasons.append(str(exc))

    audit = None
    source = None
    try:
        audit = _latest_verified_audit()
        source = _source_fixture(str(audit["audit_id"]))
        if source is None:
            reasons.append("preview_source_fixture_not_found")
    except Exception as exc:
        reasons.append(str(exc))

    token_present = bool(settings.vercel_controlled_executor_token.strip())
    if not token_present:
        reasons.append("vercel_controlled_executor_token_missing")
    if not settings.preview_acceptance_key.strip():
        reasons.append("preview_acceptance_key_missing")
    if not settings.human_production_execution_key.strip():
        reasons.append("human_production_execution_key_missing")

    adapter_mode = settings.production_execution_adapter.strip().upper()
    if adapter_mode != "VERCEL_CONTROLLED_EXECUTOR":
        reasons.append("executor_adapter_not_vercel_controlled")
    if settings.controlled_production_executor_enabled is not True:
        reasons.append("controlled_executor_not_enabled")
    if settings.production_promotion_enabled:
        reasons.append("production_promotion_must_remain_disabled")
    if settings.production_rollback_enabled:
        reasons.append("production_rollback_must_remain_disabled")

    denylist_verified = (
        REAL_PRODUCTION_PROJECT_ID
        in set(settings.production_execution_denied_project_id_list)
    )
    if not denylist_verified:
        reasons.append("real_project_missing_from_configured_denylist")

    return {
        "status": "READY" if not reasons else "NOT_READY",
        "reasons": list(dict.fromkeys(reasons)),
        "vercel_env": (os.getenv("VERCEL_ENV") or "").strip().lower(),
        "database_source": selected.get("source"),
        "preview_isolated": bool(selected.get("preview_isolated")),
        "migrations": migrations,
        "source_commit": source_commit,
        "live_acceptance_audit_id": audit.get("audit_id") if audit else None,
        "source_fixture_available": source is not None,
        "sacrificial_project_id": project_id,
        "sacrificial_team_id": team_id,
        "real_project_id": REAL_PRODUCTION_PROJECT_ID,
        "real_project_denylist_configured": denylist_verified,
        "vercel_executor_token_present": token_present,
        "production_execution_key_present": bool(
            settings.human_production_execution_key.strip()
        ),
        "preview_acceptance_key_present": bool(
            settings.preview_acceptance_key.strip()
        ),
        "provider_write_performed": False,
        "production_traffic_changed": False,
        "production_promotion_performed": False,
        "production_rollback_performed": False,
    }


def _ensure_acceptance_row(
    *,
    run_id: uuid.UUID,
    source_commit: str,
    audit_id: str,
    source_request_id,
    project_id: str,
    team_id: str,
    denylist_verified: bool,
    target_probe: dict[str, Any],
) -> dict[str, Any]:
    with engine.begin() as db:
        existing = db.execute(text("""
          SELECT *
          FROM vercel_prepare_acceptance_runs
          WHERE id=:id
          FOR UPDATE
        """), {"id": run_id}).mappings().one_or_none()
        if existing is not None:
            return dict(existing)

        row = db.execute(text("""
          INSERT INTO vercel_prepare_acceptance_runs(
            id,acceptance_status,source_commit,control_preview_url,
            live_acceptance_audit_id,source_fixture_request_id,
            sacrificial_project_id,sacrificial_team_id,real_project_id,
            real_project_denylist_verified,target_lookup_verified,
            provider_write_performed,production_traffic_changed,
            production_promotion_performed,production_rollback_performed,
            evidence)
          VALUES(
            :id,'PRECHECKED',:source_commit,:preview_url,
            :audit_id,:source_request_id,
            :project_id,:team_id,:real_project_id,
            :denylist_verified,true,
            false,false,false,false,CAST(:evidence AS jsonb))
          RETURNING *
        """), {
            "id": run_id,
            "source_commit": source_commit,
            "preview_url": (os.getenv("VERCEL_URL") or "").strip() or None,
            "audit_id": audit_id,
            "source_request_id": source_request_id,
            "project_id": project_id,
            "team_id": team_id,
            "real_project_id": REAL_PRODUCTION_PROJECT_ID,
            "denylist_verified": denylist_verified,
            "evidence": json.dumps({
                "target_probe": target_probe,
                "step": "4A-live-preview-prepare",
                "auto_assign_custom_domains": False,
                "production_promotion_enabled": False,
                "production_rollback_enabled": False,
            }, ensure_ascii=False),
        }).mappings().one()
    return dict(row)


def _clone_fixture(
    *,
    run_id: uuid.UUID,
    source: dict[str, Any],
    audit_id: str,
) -> dict[str, Any]:
    request_id = _child_id(run_id, "request")
    sandbox_run_id = _child_id(run_id, "sandbox-run")
    workspace_id = _child_id(run_id, "workspace")
    openhands_id = _child_id(run_id, "openhands")
    test_id = _child_id(run_id, "test")

    artifacts = _read_source_artifacts(source["run_id"])
    test = _read_source_test(source["run_id"])

    with engine.begin() as db:
        db.execute(text("""
          INSERT INTO sandbox_build_requests(
            id,proposal_id,proposal_revision,source_fingerprint,
            executor_kind,request_status,policy_snapshot,workspace_id,
            sandbox_image,network_policy,workspace_policy,
            external_side_effects,requested_by,policy_checked_at,
            started_at,finished_at)
          VALUES(
            :id,:proposal_id,:proposal_revision,:source_fingerprint,
            'OPENHANDS','ARTIFACT_READY',CAST(:policy AS jsonb),:workspace_id,
            'VERCEL_CONTROL_SANDBOX','DENY','ISOLATED_RW',
            'DENY','vercel-prepare-live-acceptance',now(),now(),now())
          ON CONFLICT (id) DO NOTHING
        """), {
            "id": request_id,
            "proposal_id": source["proposal_id"],
            "proposal_revision": source["proposal_revision"],
            "source_fingerprint": source["source_fingerprint"],
            "workspace_id": workspace_id,
            "policy": json.dumps({
                "preview_only": True,
                "acceptance": "VERCEL_PREPARE",
                "audit_id": audit_id,
                "deployment_target": "SACRIFICIAL_ONLY",
            }, ensure_ascii=False),
        })

        db.execute(text("""
          INSERT INTO sandbox_runs(
            id,request_id,workspace_id,workspace_path,executor_kind,
            container_image,container_network,exit_code,stdout,stderr,
            finished_at)
          VALUES(
            :id,:request_id,:workspace_id,:workspace_path,'OPENHANDS',
            'VERCEL_CONTROL_SANDBOX','none',0,
            'Cloned verified Live Acceptance artifact for Step 4A','',now())
          ON CONFLICT (id) DO NOTHING
        """), {
            "id": sandbox_run_id,
            "request_id": request_id,
            "workspace_id": workspace_id,
            "workspace_path": f"vercel-preview://prepare-acceptance/{run_id}",
        })

        for item in artifacts:
            artifact_id = _child_id(
                run_id,
                "artifact",
                str(item["relative_path"]),
            )
            db.execute(text("""
              INSERT INTO sandbox_artifacts(
                id,run_id,relative_path,sha256,byte_size,media_type)
              VALUES(:id,:run_id,:path,:sha,:size,:media)
              ON CONFLICT (id) DO NOTHING
            """), {
                "id": artifact_id,
                "run_id": sandbox_run_id,
                "path": item["relative_path"],
                "sha": item["sha256"],
                "size": item["byte_size"],
                "media": item["media_type"],
            })
            db.execute(text("""
              INSERT INTO sandbox_artifact_contents(
                artifact_id,content_bytes,content_sha256)
              VALUES(:artifact_id,:content_bytes,:sha)
              ON CONFLICT (artifact_id) DO NOTHING
            """), {
                "artifact_id": artifact_id,
                "content_bytes": bytes(item["content_bytes"]),
                "sha": item["content_sha256"],
            })

        db.execute(text("""
          INSERT INTO sandbox_test_results(
            id,run_id,test_command,exit_code,stdout,stderr,passed)
          VALUES(
            :id,:run_id,:command,:exit_code,:stdout,:stderr,true)
          ON CONFLICT (id) DO NOTHING
        """), {
            "id": test_id,
            "run_id": sandbox_run_id,
            "command": test["test_command"],
            "exit_code": int(test["exit_code"]),
            "stdout": str(test["stdout"] or "")[-20000:],
            "stderr": str(test["stderr"] or "")[-20000:],
        })

        db.execute(text("""
          INSERT INTO openhands_executions(
            id,request_id,run_id,cli_version,model_name,inner_runtime,
            network_policy,gateway_mode,task_sha256,exit_code,trace_jsonl,
            budget_status,gateway_request_count,prompt_tokens,
            completion_tokens,total_tokens,estimated_cost_usd,
            budget_snapshot,live_model_verified,finished_at)
          VALUES(
            :id,:request_id,:run_id,:cli_version,:model_name,:inner_runtime,
            'DENY','PROXY',:task_sha256,0,:trace_jsonl,
            'WITHIN_BUDGET',:gateway_request_count,:prompt_tokens,
            :completion_tokens,:total_tokens,:estimated_cost_usd,
            CAST(:budget_snapshot AS jsonb),true,now())
          ON CONFLICT (id) DO NOTHING
        """), {
            "id": openhands_id,
            "request_id": request_id,
            "run_id": sandbox_run_id,
            "cli_version": source["cli_version"],
            "model_name": source["model_name"],
            "inner_runtime": source["inner_runtime"],
            "task_sha256": source["task_sha256"],
            "trace_jsonl": json.dumps({
                "cloned_from_request_id": str(source["request_id"]),
                "live_acceptance_audit_id": audit_id,
                "step": "4A-live-preview-prepare",
                "network": "DENY",
            }, ensure_ascii=False),
            "gateway_request_count": int(source["gateway_request_count"] or 0),
            "prompt_tokens": int(source["prompt_tokens"] or 0),
            "completion_tokens": int(source["completion_tokens"] or 0),
            "total_tokens": int(source["total_tokens"] or 0),
            "estimated_cost_usd": float(source["estimated_cost_usd"] or 0),
            "budget_snapshot": json.dumps({
                "source": "VERIFIED_LIVE_ACCEPTANCE_CLONE",
                "audit_id": audit_id,
                "budget_status": "WITHIN_BUDGET",
            }, ensure_ascii=False),
        })

    release = ensure_release_candidate(str(request_id))
    if release["release_status"] == "READY_FOR_REVIEW":
        release = decide_release_candidate(
            release["release_candidate_id"],
            decision="APPROVE",
            reason="Step 4A Preview sacrificial PREPARE acceptance fixture",
            actor="vercel-prepare-live-acceptance",
            review_package_sha256=release["review_package_sha256"],
        )
        release_id = release["release_candidate_id"]
    else:
        release_id = release["release_candidate_id"]

    with engine.connect() as db:
        row = db.execute(text("""
          SELECT rc.id AS release_candidate_id,
                 rc.release_status,
                 rc.archived_at,
                 rrp.id AS review_package_id,
                 rrp.package_sha256 AS review_package_sha256,
                 rrp.source_tree_sha256
          FROM release_candidates rc
          JOIN release_review_packages rrp
            ON rrp.release_candidate_id=rc.id
          WHERE rc.id=CAST(:id AS uuid)
        """), {"id": release_id}).mappings().one()

    if row["release_status"] != "RELEASE_APPROVED":
        raise VercelPrepareAcceptanceError(
            "Step 4A fixture failed to reach RELEASE_APPROVED"
        )
    if row["archived_at"] is not None:
        raise VercelPrepareAcceptanceError(
            "Step 4A fixture is unexpectedly archived"
        )

    result = dict(row)
    result["request_id"] = str(request_id)
    result["sandbox_run_id"] = str(sandbox_run_id)
    return result


def _record_fixture(
    run_id: uuid.UUID,
    fixture: dict[str, Any],
) -> None:
    with engine.begin() as db:
        db.execute(text("""
          UPDATE vercel_prepare_acceptance_runs
          SET acceptance_status='FIXTURE_READY',
              acceptance_request_id=CAST(:request_id AS uuid),
              release_candidate_id=CAST(:candidate_id AS uuid),
              review_package_id=CAST(:review_package_id AS uuid),
              evidence=evidence || CAST(:evidence AS jsonb)
          WHERE id=:id
        """), {
            "id": run_id,
            "request_id": fixture["request_id"],
            "candidate_id": fixture["release_candidate_id"],
            "review_package_id": fixture["review_package_id"],
            "evidence": json.dumps({
                "review_package_sha256": fixture["review_package_sha256"],
                "source_tree_sha256": fixture["source_tree_sha256"],
            }, ensure_ascii=False),
        })


def _run_row(run_id) -> dict[str, Any]:
    with engine.connect() as db:
        row = db.execute(text("""
          SELECT *
          FROM vercel_prepare_acceptance_runs
          WHERE id=CAST(:id AS uuid)
        """), {"id": run_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Vercel PREPARE acceptance run not found")
    return dict(row)


def _sync_execution(
    run_id,
    execution: dict[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    with engine.begin() as db:
        row = db.execute(text("""
          UPDATE vercel_prepare_acceptance_runs
          SET acceptance_status=:status,
              execution_id=COALESCE(execution_id,CAST(:execution_id AS uuid)),
              execution_sha256=:execution_sha256,
              candidate_vercel_deployment_id=:candidate_id,
              candidate_vercel_url=:candidate_url,
              prepare_outcome=:prepare_outcome,
              prepare_write_count=:prepare_write_count,
              provider_write_performed=(:prepare_write_count=1),
              production_traffic_changed=false,
              production_promotion_performed=false,
              production_rollback_performed=false,
              evidence=evidence || CAST(:evidence AS jsonb)
          WHERE id=CAST(:id AS uuid)
          RETURNING *
        """), {
            "id": run_id,
            "status": status,
            "execution_id": execution["id"],
            "execution_sha256": execution["execution_sha256"],
            "candidate_id": execution.get("candidate_vercel_deployment_id"),
            "candidate_url": execution.get("candidate_vercel_url"),
            "prepare_outcome": execution.get("prepare_outcome"),
            "prepare_write_count": int(execution.get("prepare_write_count") or 0),
            "evidence": json.dumps({
                "execution_status": execution.get("execution_status"),
                "executor_adapter": execution.get("executor_adapter"),
                "production_vercel_deployment_id": (
                    execution.get("production_vercel_deployment_id")
                ),
                "previous_production_deployment_id": (
                    execution.get("previous_production_deployment_id")
                ),
            }, ensure_ascii=False),
        }).mappings().one()
    return dict(row)


def start_prepare_acceptance() -> dict[str, Any]:
    _preview_runtime()
    source_commit = _source_commit()
    project_id, team_id = _single_target()
    audit = _latest_verified_audit()
    source = _source_fixture(str(audit["audit_id"]))
    if source is None:
        raise VercelPrepareAcceptanceError(
            "Verified Preview source fixture is unavailable; "
            "run controlled Preview Live Acceptance first"
        )

    adapter = _configured_adapter()
    denylist_verified = _denylist_probe(adapter, team_id=team_id)
    if not denylist_verified:
        raise VercelPrepareAcceptanceError(
            "Real Production project denylist probe failed"
        )

    probe = adapter.probe_target({
        "target_provider": "VERCEL",
        "target_environment": "production",
        "target_project_id": project_id,
        "target_team_id": team_id,
    })
    if probe.get("provider_write_performed") is not False:
        raise VercelPrepareAcceptanceError(
            "Sacrificial target probe unexpectedly reported a provider write"
        )

    run_id = _acceptance_id(
        source_commit=source_commit,
        audit_id=str(audit["audit_id"]),
        project_id=project_id,
        team_id=team_id,
    )
    current = _ensure_acceptance_row(
        run_id=run_id,
        source_commit=source_commit,
        audit_id=str(audit["audit_id"]),
        source_request_id=source["request_id"],
        project_id=project_id,
        team_id=team_id,
        denylist_verified=denylist_verified,
        target_probe=probe,
    )

    if current["acceptance_status"] in {
        "PREPARE_UNKNOWN",
        "PREPARE_PENDING",
        "READY_FOR_PROMOTION",
        "PROMOTE_AUTHORIZED",
        "CLEANED_UP",
    }:
        return current

    fixture = _clone_fixture(
        run_id=run_id,
        source=source,
        audit_id=str(audit["audit_id"]),
    )
    _record_fixture(run_id, fixture)

    plan = create_deployment_plan(
        fixture["release_candidate_id"],
        target_project_id=project_id,
        target_team_id=team_id,
        actor="vercel-prepare-live-acceptance",
    )
    if plan["plan_status"] == "PENDING_AUTHORIZATION":
        plan = decide_deployment_authorization(
            plan["id"],
            decision="AUTHORIZE",
            reason="Step 4A sacrificial Preview PREPARE acceptance",
            actor="vercel-prepare-live-acceptance",
            plan_sha256=plan["plan_sha256"],
        )
        plan_id = plan["deployment_plan_id"]
    else:
        plan_id = str(plan["id"])

    execution = create_production_release_execution(
        plan_id,
        actor="vercel-prepare-live-acceptance",
    )
    with engine.begin() as db:
        db.execute(text("""
          UPDATE vercel_prepare_acceptance_runs
          SET acceptance_status='EXECUTION_CREATED',
              deployment_plan_id=CAST(:plan_id AS uuid),
              execution_id=CAST(:execution_id AS uuid),
              execution_sha256=:execution_sha256
          WHERE id=:id
        """), {
            "id": run_id,
            "plan_id": plan_id,
            "execution_id": execution["id"],
            "execution_sha256": execution["execution_sha256"],
        })

    prepared = prepare_vercel_candidate(
        execution["id"],
        actor="vercel-prepare-live-acceptance",
        adapter=adapter,
    )

    if prepared["execution_status"] == "READY_FOR_PROMOTION":
        status = "READY_FOR_PROMOTION"
    elif prepared["execution_status"] == "PREPARE_UNKNOWN":
        status = "PREPARE_UNKNOWN"
    elif prepared["execution_status"] == "PREPARING":
        status = "PREPARE_PENDING"
    else:
        status = "FAILED"

    return _sync_execution(run_id, prepared, status=status)


def reconcile_prepare_acceptance(run_id) -> dict[str, Any]:
    _preview_runtime()
    row = _run_row(run_id)
    if row["acceptance_status"] in {
        "READY_FOR_PROMOTION",
        "PROMOTE_AUTHORIZED",
        "CLEANED_UP",
    }:
        return row
    if row["acceptance_status"] not in {
        "PREPARE_UNKNOWN",
        "PREPARE_PENDING",
    }:
        raise VercelPrepareAcceptanceError(
            "Acceptance run is not awaiting PREPARE reconciliation"
        )

    project_id, team_id = _single_target()
    if (
        row["sacrificial_project_id"] != project_id
        or row["sacrificial_team_id"] != team_id
    ):
        raise VercelPrepareAcceptanceError(
            "Configured sacrificial target changed after acceptance started"
        )

    adapter = _configured_adapter()
    execution = reconcile_vercel_prepare(
        row["execution_id"],
        actor="vercel-prepare-live-acceptance-reconcile",
        adapter=adapter,
    )
    if execution["execution_status"] == "READY_FOR_PROMOTION":
        status = "READY_FOR_PROMOTION"
    elif execution["execution_status"] in {"PREPARE_UNKNOWN", "PREPARING"}:
        status = "PREPARE_UNKNOWN"
    else:
        status = "FAILED"
    return _sync_execution(run_id, execution, status=status)


def authorize_prepare_acceptance(run_id) -> dict[str, Any]:
    _preview_runtime()
    row = _run_row(run_id)
    if row["acceptance_status"] == "PROMOTE_AUTHORIZED":
        return row
    if row["acceptance_status"] != "READY_FOR_PROMOTION":
        raise VercelPrepareAcceptanceError(
            "Human PROMOTE authorization requires READY_FOR_PROMOTION"
        )

    execution = get_production_release_execution(row["execution_id"])
    if int(execution.get("prepare_write_count") or 0) != 1:
        raise VercelPrepareAcceptanceError(
            "Step 4A requires exactly one PREPARE provider write"
        )
    if execution.get("production_vercel_deployment_id") is not None:
        raise VercelPrepareAcceptanceError(
            "Step 4A forbids any Production deployment pointer"
        )
    if execution.get("previous_production_deployment_id") is not None:
        raise VercelPrepareAcceptanceError(
            "Step 4A forbids capturing a rollback pointer"
        )

    decision = decide_production_execution(
        execution["id"],
        decision="PROMOTE",
        reason="Step 4A human gate acceptance only; no provider promotion",
        actor="vercel-prepare-live-acceptance-human",
        execution_sha256=execution["execution_sha256"],
    )
    if decision.get("provider_write_performed") is not False:
        raise VercelPrepareAcceptanceError(
            "Step 4A human PROMOTE gate unexpectedly performed provider write"
        )
    if decision.get("production_traffic_changed") is not False:
        raise VercelPrepareAcceptanceError(
            "Step 4A human PROMOTE gate unexpectedly changed traffic"
        )

    with engine.begin() as db:
        saved = db.execute(text("""
          UPDATE vercel_prepare_acceptance_runs
          SET acceptance_status='PROMOTE_AUTHORIZED',
              human_decision_id=CAST(:decision_id AS uuid),
              decision_sha256=:decision_sha256,
              provider_write_performed=true,
              production_traffic_changed=false,
              production_promotion_performed=false,
              production_rollback_performed=false,
              finished_at=now(),
              evidence=evidence || CAST(:evidence AS jsonb)
          WHERE id=CAST(:id AS uuid)
          RETURNING *
        """), {
            "id": run_id,
            "decision_id": decision["id"],
            "decision_sha256": decision["decision_sha256"],
            "evidence": json.dumps({
                "promotion_authorized": True,
                "provider_promotion_performed": False,
                "provider_write_count": 1,
                "production_traffic_changed": False,
            }, ensure_ascii=False),
        }).mappings().one()
    return dict(saved)


def cleanup_prepare_acceptance(run_id) -> dict[str, Any]:
    _preview_runtime()
    row = _run_row(run_id)
    if row["acceptance_status"] == "CLEANED_UP":
        return row
    if row["acceptance_status"] not in {
        "READY_FOR_PROMOTION",
        "PROMOTE_AUTHORIZED",
        "FAILED",
        "BLOCKED",
    }:
        raise VercelPrepareAcceptanceError(
            "Acceptance cleanup requires a terminal/non-running acceptance state"
        )

    with engine.begin() as db:
        if row["release_candidate_id"] is not None:
            db.execute(text("""
              UPDATE release_candidates
              SET archived_at=COALESCE(archived_at,now()),
                  archive_reason=COALESCE(
                    archive_reason,
                    'Step 4A sacrificial PREPARE acceptance cleanup'
                  )
              WHERE id=:candidate_id
            """), {"candidate_id": row["release_candidate_id"]})

        saved = db.execute(text("""
          UPDATE vercel_prepare_acceptance_runs
          SET acceptance_status='CLEANED_UP',
              finished_at=COALESCE(finished_at,now()),
              production_traffic_changed=false,
              production_promotion_performed=false,
              production_rollback_performed=false,
              evidence=evidence || CAST(:evidence AS jsonb)
          WHERE id=CAST(:id AS uuid)
          RETURNING *
        """), {
            "id": run_id,
            "evidence": json.dumps({
                "fixture_archived": True,
                "sacrificial_deployment_deleted": False,
                "real_project_modified": False,
            }, ensure_ascii=False),
        }).mappings().one()
    return dict(saved)


def get_prepare_acceptance(run_id) -> dict[str, Any]:
    return _run_row(run_id)
