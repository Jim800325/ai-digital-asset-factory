import hashlib
import json
import mimetypes
import os
import uuid
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import database_selection, engine
from app.release_gate import ensure_release_candidate


class PreviewAcceptanceBridgeError(RuntimeError):
    pass


_NAMESPACE = uuid.UUID("5c2d9b7a-3d0f-4f67-a8fd-3ca3c8360bf7")


def _preview_uuid(audit_id: str, kind: str, role: str = "") -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{audit_id}|{kind}|{role}")


def _require_preview_runtime() -> dict[str, Any]:
    vercel_env = (os.getenv("VERCEL_ENV") or "").strip().lower()
    if vercel_env != "preview":
        raise PreviewAcceptanceBridgeError(
            "Preview Acceptance Bridge is only available in Vercel Preview"
        )
    if settings.deployment_authorization_preview_only is not True:
        raise PreviewAcceptanceBridgeError(
            "Preview Acceptance Bridge requires preview-only mode"
        )

    selected = database_selection()
    if (
        selected.get("source") != "PREVIEW_DATABASE_URL"
        or selected.get("preview_isolated") is not True
    ):
        raise PreviewAcceptanceBridgeError(
            "Preview Acceptance Bridge requires PREVIEW_DATABASE_URL"
        )
    return selected


def _verify_result(result: dict[str, Any]) -> None:
    required_true = {
        "live_model_verified": result.get("live_model_verified") is True,
        "tests_passed": result.get("tests_passed") is True,
    }
    failed = [name for name, ok in required_true.items() if not ok]
    if failed:
        raise PreviewAcceptanceBridgeError(
            "Live Acceptance contract failed: " + ", ".join(failed)
        )

    expected = {
        "acceptance_status": "PASSED",
        "gateway_mode": "PROXY",
        "budget_status": "WITHIN_BUDGET",
        "external_side_effects": "DENY",
    }
    mismatches = [
        name
        for name, value in expected.items()
        if result.get(name) != value
    ]
    if mismatches:
        raise PreviewAcceptanceBridgeError(
            "Live Acceptance contract mismatch: " + ", ".join(mismatches)
        )
    if result.get("deployment_enabled") is not False:
        raise PreviewAcceptanceBridgeError(
            "Live Acceptance unexpectedly enabled deployment"
        )
    if result.get("release_approved") is not False:
        raise PreviewAcceptanceBridgeError(
            "Live Acceptance unexpectedly approved release"
        )


def _artifact_manifest(
    artifact_payloads: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    if not artifact_payloads:
        raise PreviewAcceptanceBridgeError("Live Acceptance produced no artifacts")

    manifest = []
    seen = set()
    for item in sorted(
        artifact_payloads,
        key=lambda value: str(value.get("relative_path") or ""),
    ):
        path = str(item.get("relative_path") or "").strip()
        data = item.get("content_bytes")
        if (
            not path
            or path.startswith("/")
            or ".." in path.split("/")
            or path in seen
            or not isinstance(data, (bytes, bytearray))
        ):
            raise PreviewAcceptanceBridgeError(
                "Invalid Preview Live Acceptance artifact payload"
            )
        seen.add(path)
        raw = bytes(data)
        sha = hashlib.sha256(raw).hexdigest()
        manifest.append({
            "relative_path": path,
            "sha256": sha,
            "byte_size": len(raw),
            "media_type": (
                mimetypes.guess_type(path)[0] or "application/octet-stream"
            ),
            "content_bytes": raw,
        })

    acceptance_rows = [
        {
            "relative_path": item["relative_path"],
            "sha256": item["sha256"],
            "byte_size": item["byte_size"],
        }
        for item in manifest
    ]
    source_tree = hashlib.sha256(
        json.dumps(
            acceptance_rows,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return manifest, source_tree


def persist_preview_live_acceptance_fixture(
    result: dict[str, Any],
    artifact_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    selected = _require_preview_runtime()
    _verify_result(result)

    audit_id = str(result.get("audit_id") or "").strip()
    if len(audit_id) < 8:
        raise PreviewAcceptanceBridgeError("Live Acceptance audit_id is invalid")

    manifest, calculated_tree = _artifact_manifest(artifact_payloads)
    reported_tree = str(result.get("source_tree_sha256") or "").strip().lower()
    if not reported_tree or reported_tree != calculated_tree:
        raise PreviewAcceptanceBridgeError(
            "Live Acceptance artifact source tree does not match persisted bytes"
        )

    opportunity_id = _preview_uuid(audit_id, "opportunity")
    proposal_id = _preview_uuid(audit_id, "proposal")
    proposal_decision_id = _preview_uuid(audit_id, "proposal-decision")
    source_fingerprint = hashlib.sha256(
        f"preview-live-acceptance|{audit_id}|{calculated_tree}".encode("utf-8")
    ).hexdigest()
    task_sha = hashlib.sha256(
        b"preview-live-acceptance-controlled-openhands-task"
    ).hexdigest()

    with engine.begin() as db:
        db.execute(text("""
          INSERT INTO digital_asset_opportunities(
            id,fingerprint,title,asset_type,problem,target_customer,
            monetization_model,source_url,status)
          VALUES(
            :id,:fingerprint,:title,'MICRO_SAAS_TOOL',
            'Preview-only controlled Live Acceptance fixture',
            'Internal release engineering',
            'NOT_FOR_SALE',
            :source_url,'WATCH')
          ON CONFLICT (id) DO NOTHING
        """), {
            "id": opportunity_id,
            "fingerprint": source_fingerprint,
            "title": f"[PREVIEW_ONLY] Live Acceptance {audit_id}",
            "source_url": f"vercel-preview://live-acceptance/{audit_id}",
        })

        db.execute(text("""
          INSERT INTO build_proposals(
            id,opportunity_id,revision,proposal_status,title,objective,
            artifact_type,scope,success_criteria,constraints,sandbox_policy,
            proposed_stack,source_snapshot,source_fingerprint,
            generator_version,requires_human_approval,execution_enabled,
            approved_at)
          VALUES(
            :id,:opportunity_id,1,'APPROVED',:title,:objective,
            'API_SERVICE',CAST(:scope AS jsonb),CAST(:success AS jsonb),
            CAST(:constraints AS jsonb),CAST(:policy AS jsonb),
            CAST(:stack AS jsonb),CAST(:snapshot AS jsonb),:fingerprint,
            'preview-live-acceptance-bridge-v1',true,false,now())
          ON CONFLICT (id) DO NOTHING
        """), {
            "id": proposal_id,
            "opportunity_id": opportunity_id,
            "title": f"[PREVIEW_ONLY] Controlled artifact {audit_id}",
            "objective": (
                "Persist exact controlled Live Acceptance artifact bytes "
                "for Deployment Authorization Preview acceptance only."
            ),
            "scope": json.dumps({
                "preview_only": True,
                "audit_id": audit_id,
                "source_tree_sha256": calculated_tree,
            }),
            "success": json.dumps([
                "Artifact bytes exactly match the Live Acceptance hashes.",
                "Review Package content snapshot is complete.",
                "No production deployment occurs.",
            ]),
            "constraints": json.dumps([
                "Preview database only.",
                "No production execution.",
                "No deployment executor.",
            ]),
            "policy": json.dumps({
                "network": "CONTROLLED_GATEWAY_THEN_DENY_ALL",
                "deployment": "DENY",
                "external_side_effects": "DENY",
            }),
            "stack": json.dumps(["Python", "unittest"]),
            "snapshot": json.dumps({
                "audit_id": audit_id,
                "source_tree_sha256": calculated_tree,
                "live_model_verified": True,
            }),
            "fingerprint": source_fingerprint,
        })

        db.execute(text("""
          INSERT INTO build_proposal_decisions(
            id,proposal_id,proposal_revision,decision,reason,actor)
          VALUES(
            :id,:proposal_id,1,'APPROVE',
            'Preview-only controlled Live Acceptance bridge',
            'preview-acceptance-bridge')
          ON CONFLICT (id) DO NOTHING
        """), {
            "id": proposal_decision_id,
            "proposal_id": proposal_id,
        })

    fixtures = []
    for role in ("authorize", "reject"):
        request_id = _preview_uuid(audit_id, "request", role)
        run_id = _preview_uuid(audit_id, "run", role)
        workspace_id = _preview_uuid(audit_id, "workspace", role)
        execution_id = _preview_uuid(audit_id, "openhands-execution", role)
        test_id = _preview_uuid(audit_id, "test-result", role)

        with engine.begin() as db:
            db.execute(text("""
              INSERT INTO sandbox_build_requests(
                id,proposal_id,proposal_revision,source_fingerprint,
                executor_kind,request_status,policy_snapshot,workspace_id,
                sandbox_image,network_policy,workspace_policy,
                external_side_effects,requested_by,policy_checked_at,
                started_at,finished_at)
              VALUES(
                :id,:proposal_id,1,:fingerprint,
                'OPENHANDS','ARTIFACT_READY',CAST(:policy AS jsonb),:workspace_id,
                'VERCEL_CONTROL_SANDBOX','INTERNAL_GATEWAY_ONLY','ISOLATED_RW',
                'DENY','preview-acceptance-bridge',now(),now(),now())
              ON CONFLICT (id) DO NOTHING
            """), {
                "id": request_id,
                "proposal_id": proposal_id,
                "fingerprint": source_fingerprint,
                "workspace_id": workspace_id,
                "policy": json.dumps({
                    "preview_only": True,
                    "role": role,
                    "audit_id": audit_id,
                    "deployment": "DENY",
                }),
            })

            db.execute(text("""
              INSERT INTO sandbox_runs(
                id,request_id,workspace_id,workspace_path,executor_kind,
                container_image,container_network,exit_code,stdout,stderr,
                finished_at)
              VALUES(
                :id,:request_id,:workspace_id,:workspace_path,'OPENHANDS',
                'VERCEL_CONTROL_SANDBOX','internal-gateway',0,
                'Controlled Preview Live Acceptance completed','',now())
              ON CONFLICT (id) DO NOTHING
            """), {
                "id": run_id,
                "request_id": request_id,
                "workspace_id": workspace_id,
                "workspace_path": (
                    f"vercel-preview://live-acceptance/{audit_id}/{role}"
                ),
            })

            for artifact in manifest:
                artifact_id = _preview_uuid(
                    audit_id,
                    "artifact",
                    role + "|" + artifact["relative_path"],
                )
                db.execute(text("""
                  INSERT INTO sandbox_artifacts(
                    id,run_id,relative_path,sha256,byte_size,media_type)
                  VALUES(:id,:run_id,:path,:sha,:size,:media)
                  ON CONFLICT (id) DO NOTHING
                """), {
                    "id": artifact_id,
                    "run_id": run_id,
                    "path": artifact["relative_path"],
                    "sha": artifact["sha256"],
                    "size": artifact["byte_size"],
                    "media": artifact["media_type"],
                })
                db.execute(text("""
                  INSERT INTO sandbox_artifact_contents(
                    artifact_id,content_bytes,content_sha256)
                  VALUES(:artifact_id,:content_bytes,:sha)
                  ON CONFLICT (artifact_id) DO NOTHING
                """), {
                    "artifact_id": artifact_id,
                    "content_bytes": artifact["content_bytes"],
                    "sha": artifact["sha256"],
                })

            db.execute(text("""
              INSERT INTO sandbox_test_results(
                id,run_id,test_command,exit_code,stdout,stderr,passed)
              VALUES(
                :id,:run_id,
                'python -m unittest discover -s tests -q',
                0,:stdout,'',true)
              ON CONFLICT (id) DO NOTHING
            """), {
                "id": test_id,
                "run_id": run_id,
                "stdout": str(result.get("test_log_tail") or "")[-20000:],
            })

            db.execute(text("""
              INSERT INTO openhands_executions(
                id,request_id,run_id,cli_version,model_name,inner_runtime,
                network_policy,gateway_mode,task_sha256,exit_code,trace_jsonl,
                budget_status,gateway_request_count,prompt_tokens,
                completion_tokens,total_tokens,estimated_cost_usd,
                budget_snapshot,live_model_verified,finished_at)
              VALUES(
                :id,:request_id,:run_id,:cli_version,:model,'process',
                'INTERNAL_GATEWAY_ONLY','PROXY',:task_sha,0,:trace,
                'WITHIN_BUDGET',:requests,:prompt_tokens,:completion_tokens,
                :total_tokens,:cost,CAST(:budget_snapshot AS jsonb),true,now())
              ON CONFLICT (id) DO NOTHING
            """), {
                "id": execution_id,
                "request_id": request_id,
                "run_id": run_id,
                "cli_version": settings.openhands_cli_version,
                "model": str(result.get("model") or "gpt-5.6-luna"),
                "task_sha": task_sha,
                "trace": json.dumps({
                    "preview_only": True,
                    "audit_id": audit_id,
                    "source_tree_sha256": calculated_tree,
                }),
                "requests": int(result.get("gateway_request_count") or 0),
                "prompt_tokens": int(result.get("prompt_tokens") or 0),
                "completion_tokens": int(result.get("completion_tokens") or 0),
                "total_tokens": int(result.get("total_tokens") or 0),
                "cost": float(result.get("estimated_cost_usd") or 0),
                "budget_snapshot": json.dumps({
                    "budget_status": "WITHIN_BUDGET",
                    "preview_only": True,
                }),
            })

        release = ensure_release_candidate(str(request_id))
        if release["release_status"] != "READY_FOR_REVIEW":
            raise PreviewAcceptanceBridgeError(
                "Preview fixture did not reach READY_FOR_REVIEW"
            )
        if release["review_snapshot_complete"] is not True:
            raise PreviewAcceptanceBridgeError(
                "Preview fixture Review Package snapshot is incomplete"
            )
        if release["deployment_enabled"] is not False:
            raise PreviewAcceptanceBridgeError(
                "Preview fixture unexpectedly enabled deployment"
            )

        fixtures.append({
            "role": role,
            "request_id": str(request_id),
            "run_id": str(run_id),
            "release_candidate_id": release["release_candidate_id"],
            "review_package_id": release["review_package_id"],
            "review_package_sha256": release["review_package_sha256"],
            "review_source_tree_sha256": release["source_tree_sha256"],
            "acceptance_provenance_tree_sha256": calculated_tree,
            "release_status": release["release_status"],
            "review_snapshot_complete": release["review_snapshot_complete"],
            "deployment_enabled": False,
            "execution_enabled": False,
        })

    return {
        "bridge_status": "READY_FOR_REGISTRY_BINDING",
        "preview_only": True,
        "database_source": selected["source"],
        "audit_id": audit_id,
        "source_tree_sha256": calculated_tree,
        "artifact_count": len(manifest),
        "artifact_contents_persisted": True,
        "fixtures": fixtures,
        "deployment_enabled": False,
        "execution_enabled": False,
        "production_deployment_executed": False,
    }
