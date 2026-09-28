import hashlib
import json

from sqlalchemy import text

from app.db import engine


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
            if existing["release_status"] in {"RELEASE_APPROVED","RELEASE_REJECTED"}:
                return {
                    "release_candidate_id":str(existing["id"]),
                    "release_status":existing["release_status"],
                    "created":False,
                    "artifact_manifest_sha256":existing["artifact_manifest_sha256"],
                }
            candidate_id=existing["id"]
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
            created=False

    return {
        "release_candidate_id":str(candidate_id),
        "request_id":str(source["request_id"]),
        "run_id":str(source["run_id"]),
        "release_status":status,
        "live_validation_verified":live_ok,
        "artifact_manifest_sha256":manifest_sha,
        "deployment_enabled":False,
        "created":created,
    }


def decide_release_candidate(candidate_id,*,decision:str,reason:str,actor:str="human"):
    normalized=(decision or "").upper().strip()
    clean_reason=(reason or "").strip()
    clean_actor=(actor or "human").strip() or "human"
    if normalized not in {"APPROVE","REJECT"}:
        raise ValueError("decision must be APPROVE or REJECT")
    if len(clean_reason)<3:
        raise ValueError("reason must contain at least 3 characters")

    with engine.begin() as db:
        row=db.execute(text("""
          SELECT rc.id,rc.release_status,rc.live_validation_verified,
                 rc.deployment_enabled,
                 oe.gateway_mode,oe.budget_status,oe.live_model_verified,
                 sbr.request_status,
                 bp.proposal_status,bp.execution_enabled
          FROM release_candidates rc
          JOIN sandbox_build_requests sbr ON sbr.id=rc.request_id
          JOIN build_proposals bp ON bp.id=rc.proposal_id
          LEFT JOIN openhands_executions oe ON oe.request_id=rc.request_id
          WHERE rc.id=CAST(:id AS uuid)
          FOR UPDATE OF rc,bp
        """),{"id":candidate_id}).mappings().one_or_none()
        if row is None:
            raise LookupError("Release candidate not found")
        if row["release_status"] in {"RELEASE_APPROVED","RELEASE_REJECTED"}:
            raise RuntimeError("Release decision is already terminal")
        if row["deployment_enabled"]:
            raise RuntimeError("Release gate must never enable deployment")

        if normalized=="APPROVE":
            live_ok=(
                row["gateway_mode"]=="PROXY"
                and row["budget_status"]=="WITHIN_BUDGET"
                and row["live_model_verified"] is True
                and row["request_status"]=="ARTIFACT_READY"
                and row["proposal_status"]=="APPROVED"
                and row["execution_enabled"] is False
            )
            if not live_ok:
                raise RuntimeError("Controlled Live LLM Acceptance has not passed")
            status="RELEASE_APPROVED"
            ts_column="approved_at"
        else:
            status="RELEASE_REJECTED"
            ts_column="rejected_at"

        db.execute(text(f"""
          UPDATE release_candidates
          SET release_status=:status,{ts_column}=now(),deployment_enabled=false
          WHERE id=:id
        """),{"status":status,"id":row["id"]})
        decision_id=db.execute(text("""
          INSERT INTO release_decisions(
            release_candidate_id,candidate_status,decision,reason,actor)
          VALUES(:id,:status,:decision,:reason,:actor)
          RETURNING id
        """),{
            "id":row["id"],
            "status":status,
            "decision":normalized,
            "reason":clean_reason[:4000],
            "actor":clean_actor[:200],
        }).scalar_one()

    return {
        "release_candidate_id":str(row["id"]),
        "release_status":status,
        "decision_id":str(decision_id),
        "live_validation_verified":normalized=="APPROVE",
        "deployment_enabled":False,
    }
