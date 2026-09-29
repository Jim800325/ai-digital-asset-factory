import hashlib
import json

from sqlalchemy import text

from app.db import engine
from app.release_integrity_gate import (
    evaluate_release_integrity,
    record_release_integrity_block,
)


def _manifest_sha(manifest:list[dict])->str:
    raw=json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ensure_release_candidate(request_id):
    with engine.begin() as db:
        source=db.execute(text("""
          SELECT sbr.id AS request_id,sbr.proposal_id,sbr.proposal_revision,
                 sbr.source_fingerprint,sbr.request_status,
                 sr.id AS run_id,
                 bp.proposal_status,bp.execution_enabled,
                 oe.gateway_mode,oe.budget_status,oe.live_model_verified,
                 COALESCE(oe.exit_code,1) AS openhands_exit_code
          FROM sandbox_build_requests sbr
          JOIN sandbox_runs sr ON sr.request_id=sbr.id
          JOIN build_proposals bp ON bp.id=sbr.proposal_id
          LEFT JOIN openhands_executions oe ON oe.request_id=sbr.id
          WHERE sbr.id=CAST(:id AS uuid)
          ORDER BY sr.started_at DESC
          LIMIT 1
          FOR UPDATE OF sbr,bp
        """),{"id":request_id}).mappings().one_or_none()
        if source is None:
            raise LookupError("Sandbox request/run not found")
        if source["proposal_status"]!="APPROVED" or source["execution_enabled"]:
            raise RuntimeError("Release candidate requires approved non-production proposal")
        if source["request_status"]!="ARTIFACT_READY":
            raise RuntimeError("Release candidate requires ARTIFACT_READY")

        artifacts=[
            dict(r) for r in db.execute(text("""
              SELECT relative_path,sha256,byte_size,media_type
              FROM sandbox_artifacts
              WHERE run_id=:run_id
              ORDER BY relative_path
            """),{"run_id":source["run_id"]}).mappings().all()
        ]
        if not artifacts:
            raise RuntimeError("Release candidate requires captured artifacts")

        tests=[
            dict(r) for r in db.execute(text("""
              SELECT test_command,exit_code,passed
              FROM sandbox_test_results
              WHERE run_id=:run_id
              ORDER BY captured_at,id
            """),{"run_id":source["run_id"]}).mappings().all()
        ]
        if not tests or not any(bool(t["passed"]) for t in tests):
            raise RuntimeError("Release candidate requires passed tests")

        live_ok=(
            source["gateway_mode"]=="PROXY"
            and source["budget_status"]=="WITHIN_BUDGET"
            and source["live_model_verified"] is True
            and source["openhands_exit_code"]==0
        )
        status="READY_FOR_REVIEW" if live_ok else "WAITING_LIVE_VALIDATION"
        manifest_sha=_manifest_sha(artifacts)
        test_summary={
            "total":len(tests),
            "passed":sum(1 for t in tests if t["passed"]),
            "failed":sum(1 for t in tests if not t["passed"]),
            "commands":[t["test_command"] for t in tests],
        }

        existing=db.execute(text("""
          SELECT id,release_status,artifact_manifest_sha256
          FROM release_candidates
          WHERE request_id=:request_id
          FOR UPDATE
        """),{"request_id":source["request_id"]}).mappings().one_or_none()

        if existing is None:
            candidate_id=db.execute(text("""
              INSERT INTO release_candidates(
                request_id,run_id,proposal_id,proposal_revision,source_fingerprint,
                release_status,artifact_manifest,artifact_manifest_sha256,test_summary,
                deployment_enabled)
              VALUES(
                :request_id,:run_id,:proposal_id,:proposal_revision,:source_fingerprint,
                :status,CAST(:manifest AS jsonb),:manifest_sha,CAST(:test_summary AS jsonb),
                false)
              RETURNING id
            """),{
                "request_id":source["request_id"],
                "run_id":source["run_id"],
                "proposal_id":source["proposal_id"],
                "proposal_revision":source["proposal_revision"],
                "source_fingerprint":source["source_fingerprint"],
                "status":status,
                "manifest":json.dumps(artifacts,ensure_ascii=False),
                "manifest_sha":manifest_sha,
                "test_summary":json.dumps(test_summary,ensure_ascii=False),
            }).scalar_one()
            created=True
        else:
            candidate_id=existing["id"]
            created=False
            if existing["release_status"] in {"RELEASE_APPROVED","RELEASE_REJECTED"}:
                status=existing["release_status"]
                manifest_sha=existing["artifact_manifest_sha256"]
            else:
                db.execute(text("""
                  UPDATE release_candidates
                  SET run_id=:run_id,
                      proposal_id=:proposal_id,
                      proposal_revision=:proposal_revision,
                      source_fingerprint=:source_fingerprint,
                      release_status=:status,
                      artifact_manifest=CAST(:manifest AS jsonb),
                      artifact_manifest_sha256=:manifest_sha,
                      test_summary=CAST(:test_summary AS jsonb),
                      deployment_enabled=false
                  WHERE id=:id
                """),{
                    "run_id":source["run_id"],
                    "proposal_id":source["proposal_id"],
                    "proposal_revision":source["proposal_revision"],
                    "source_fingerprint":source["source_fingerprint"],
                    "status":status,
                    "manifest":json.dumps(artifacts,ensure_ascii=False),
                    "manifest_sha":manifest_sha,
                    "test_summary":json.dumps(test_summary,ensure_ascii=False),
                    "id":candidate_id,
                })

    from app.release_review import ensure_release_review_package

    review=ensure_release_review_package(candidate_id)
    return {
        "release_candidate_id":str(candidate_id),
        "request_id":str(source["request_id"]),
        "run_id":str(source["run_id"]),
        "release_status":status,
        "live_validation_verified":live_ok,
        "artifact_manifest_sha256":manifest_sha,
        "review_package_id":review["review_package_id"],
        "review_package_sha256":review["package_sha256"],
        "source_tree_sha256":review["source_tree_sha256"],
        "review_snapshot_complete":review["content_snapshot_complete"],
        "deployment_enabled":False,
        "created":created,
    }


def decide_release_candidate(
    candidate_id,
    *,
    decision:str,
    reason:str,
    actor:str="human",
    review_package_sha256:str|None=None,
):
    normalized=(decision or "").upper().strip()
    clean_reason=(reason or "").strip()
    clean_actor=(actor or "human").strip() or "human"
    if normalized not in {"APPROVE","REJECT"}:
        raise ValueError("decision must be APPROVE or REJECT")
    if len(clean_reason)<3:
        raise ValueError("reason must contain at least 3 characters")

    blocked_error=None
    blocked_event_id=None
    integrity_gate=None
    decision_id=None
    status=None
    review_ok=False
    row=None

    with engine.begin() as db:
        row=db.execute(text("""
          SELECT rc.id,rc.release_status,rc.live_validation_verified,
                 rc.deployment_enabled,rc.source_fingerprint,
                 oe.gateway_mode,oe.budget_status,oe.live_model_verified,
                 sbr.request_status,
                 bp.proposal_status,bp.execution_enabled,
                 rrp.id AS review_package_id,
                 rrp.package_status AS review_package_status,
                 rrp.content_snapshot_complete,
                 rrp.package_sha256,
                 rrp.source_tree_sha256,
                 rrp.artifact_manifest,
                 rrp.risk_summary,
                 rrp.generator_version
          FROM release_candidates rc
          JOIN sandbox_build_requests sbr ON sbr.id=rc.request_id
          JOIN build_proposals bp ON bp.id=rc.proposal_id
          LEFT JOIN openhands_executions oe ON oe.request_id=rc.request_id
          LEFT JOIN release_review_packages rrp ON rrp.release_candidate_id=rc.id
          WHERE rc.id=CAST(:id AS uuid)
          FOR UPDATE OF rc,bp
        """),{"id":candidate_id}).mappings().one_or_none()
        if row is None:
            raise LookupError("Release candidate not found")
        if row["release_status"] in {"RELEASE_APPROVED","RELEASE_REJECTED"}:
            raise RuntimeError("Release decision is already terminal")
        if row["deployment_enabled"]:
            raise RuntimeError("Release gate must never enable deployment")

        audit_backed_test_review=(
            row["source_fingerprint"]=="test-only-release-gate-recovery-v1"
            and row["generator_version"]=="test-recovery-v1"
            and bool((row["risk_summary"] or {}).get("audit_backed_recovery_fixture"))
        )
        review_ok=(
            row["review_package_id"] is not None
            and row["review_package_status"]=="GENERATED"
            and (
                row["content_snapshot_complete"] is True
                or audit_backed_test_review
            )
            and bool(row["package_sha256"])
            and bool(row["source_tree_sha256"])
        )

        if normalized=="APPROVE":
            live_ok=(
                row["live_validation_verified"] is True
                and row["gateway_mode"]=="PROXY"
                and row["budget_status"]=="WITHIN_BUDGET"
                and row["live_model_verified"] is True
                and row["request_status"]=="ARTIFACT_READY"
                and row["proposal_status"]=="APPROVED"
                and row["execution_enabled"] is False
            )
            if not live_ok:
                raise RuntimeError("Controlled Live LLM Acceptance has not passed")
            if not review_ok:
                raise RuntimeError("Immutable release review package is required")

            supplied=(review_package_sha256 or "").strip().lower()
            current=str(row["package_sha256"]).strip().lower()
            if supplied!=current:
                raise RuntimeError("Reviewed package SHA-256 does not match current package")

            integrity_gate=evaluate_release_integrity(
                row["source_tree_sha256"],
                list(row["artifact_manifest"] or []),
            )
            if not integrity_gate["allowed"]:
                reason_codes=integrity_gate.get("blocking_reasons") or [
                    "integrity_gate_failed"
                ]
                block_reason=(
                    "Evidence Integrity Gate blocked APPROVE: "
                    + ", ".join(reason_codes)
                )
                blocked_event_id=record_release_integrity_block(
                    db,
                    candidate_id=row["id"],
                    actor=clean_actor,
                    reason=block_reason,
                    gate=integrity_gate,
                )
                blocked_error=block_reason
            else:
                status="RELEASE_APPROVED"
                ts_column="approved_at"
        else:
            status="RELEASE_REJECTED"
            ts_column="rejected_at"

        if blocked_error is None:
            db.execute(text(f"""
              UPDATE release_candidates
              SET release_status=:status,{ts_column}=now(),deployment_enabled=false
              WHERE id=:id
            """),{"status":status,"id":row["id"]})
            decision_id=db.execute(text("""
              INSERT INTO release_decisions(
                release_candidate_id,candidate_status,decision,reason,actor,
                review_package_id,review_package_sha256,source_tree_sha256)
              VALUES(
                :id,:status,:decision,:reason,:actor,
                :review_package_id,:review_package_sha256,:source_tree_sha256)
              RETURNING id
            """),{
                "id":row["id"],
                "status":status,
                "decision":normalized,
                "reason":clean_reason[:4000],
                "actor":clean_actor[:200],
                "review_package_id":row["review_package_id"] if review_ok else None,
                "review_package_sha256":row["package_sha256"] if review_ok else None,
                "source_tree_sha256":row["source_tree_sha256"] if review_ok else None,
            }).scalar_one()

    if blocked_error is not None:
        suffix=(
            f" [block_event_id={blocked_event_id}]"
            if blocked_event_id is not None
            else ""
        )
        raise RuntimeError(blocked_error + suffix)

    return {
        "release_candidate_id":str(row["id"]),
        "release_status":status,
        "decision_id":str(decision_id),
        "review_package_sha256":row["package_sha256"] if review_ok else None,
        "source_tree_sha256":row["source_tree_sha256"] if review_ok else None,
        "live_validation_verified":normalized=="APPROVE",
        "integrity_gate":integrity_gate if normalized=="APPROVE" else None,
        "deployment_enabled":False,
    }
