import json
from typing import Any

from sqlalchemy import text

from app.db import engine


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS live_acceptance_audits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trigger_token_sha256 text NOT NULL UNIQUE,
  acceptance_status text NOT NULL DEFAULT 'RUNNING',
  phase text NOT NULL DEFAULT 'claimed',
  provider text NOT NULL DEFAULT 'AIHUBMIX',
  model text NOT NULL,
  gateway_mode text NOT NULL DEFAULT 'PROXY',
  live_model_verified boolean NOT NULL DEFAULT false,
  budget_status text NOT NULL DEFAULT 'NOT_EVALUATED',
  gateway_request_count integer NOT NULL DEFAULT 0,
  prompt_tokens integer NOT NULL DEFAULT 0,
  completion_tokens integer NOT NULL DEFAULT 0,
  total_tokens integer NOT NULL DEFAULT 0,
  estimated_cost_usd numeric(12,6) NOT NULL DEFAULT 0,
  model_request_observed boolean NOT NULL DEFAULT false,
  tests_passed boolean NOT NULL DEFAULT false,
  artifact_count integer NOT NULL DEFAULT 0,
  source_tree_sha256 text,
  duplicate_requests integer NOT NULL DEFAULT 0,
  error text,
  result_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_live_acceptance_status
    CHECK (acceptance_status IN ('RUNNING','PASSED','FAILED')),
  CONSTRAINT chk_live_acceptance_budget_status
    CHECK (budget_status IN ('NOT_EVALUATED','WITHIN_BUDGET','BLOCKED')),
  CONSTRAINT chk_live_acceptance_nonnegative
    CHECK (
      gateway_request_count >= 0
      AND prompt_tokens >= 0
      AND completion_tokens >= 0
      AND total_tokens >= 0
      AND estimated_cost_usd >= 0
      AND artifact_count >= 0
      AND duplicate_requests >= 0
    )
);
CREATE INDEX IF NOT EXISTS idx_live_acceptance_audits_started
  ON live_acceptance_audits(started_at DESC);
"""


def ensure_live_acceptance_audit_schema() -> None:
    with engine.begin() as db:
        db.exec_driver_sql(_SCHEMA_SQL)


def _row_to_dict(row: Any) -> dict[str, Any]:
    payload=dict(row._mapping if hasattr(row,"_mapping") else row)
    if payload.get("id") is not None:
        payload["id"]=str(payload["id"])
    snapshot=payload.get("result_snapshot")
    if isinstance(snapshot,str):
        try:
            payload["result_snapshot"]=json.loads(snapshot)
        except json.JSONDecodeError:
            payload["result_snapshot"]={}
    return payload


def claim_live_acceptance(
    trigger_token_sha256: str,
    *,
    model: str,
) -> dict[str, Any]:
    with engine.begin() as db:
        inserted=db.execute(
            text("""
              INSERT INTO live_acceptance_audits(
                trigger_token_sha256,model
              )
              VALUES(:trigger_hash,:model)
              ON CONFLICT (trigger_token_sha256) DO NOTHING
              RETURNING *
            """),
            {"trigger_hash":trigger_token_sha256,"model":model},
        ).mappings().one_or_none()
        if inserted is not None:
            payload=_row_to_dict(inserted)
            payload["claimed"]=True
            return payload

        existing=db.execute(
            text("""
              UPDATE live_acceptance_audits
              SET duplicate_requests=duplicate_requests+1,
                  updated_at=now()
              WHERE trigger_token_sha256=:trigger_hash
              RETURNING *
            """),
            {"trigger_hash":trigger_token_sha256},
        ).mappings().one()
        payload=_row_to_dict(existing)
        payload["claimed"]=False
        return payload


def update_live_acceptance_phase(audit_id: str, phase: str) -> None:
    with engine.begin() as db:
        db.execute(
            text("""
              UPDATE live_acceptance_audits
              SET phase=:phase,updated_at=now()
              WHERE id=CAST(:id AS uuid)
            """),
            {"id":audit_id,"phase":phase},
        )


def finish_live_acceptance(audit_id: str, result: dict[str, Any]) -> None:
    status=str(result.get("acceptance_status") or "FAILED").upper()
    if status not in {"PASSED","FAILED"}:
        status="FAILED"
    with engine.begin() as db:
        db.execute(
            text("""
              UPDATE live_acceptance_audits
              SET acceptance_status=:status,
                  phase=:phase,
                  live_model_verified=:live_model_verified,
                  budget_status=:budget_status,
                  gateway_request_count=:gateway_request_count,
                  prompt_tokens=:prompt_tokens,
                  completion_tokens=:completion_tokens,
                  total_tokens=:total_tokens,
                  estimated_cost_usd=:estimated_cost_usd,
                  model_request_observed=:model_request_observed,
                  tests_passed=:tests_passed,
                  artifact_count=:artifact_count,
                  source_tree_sha256=:source_tree_sha256,
                  error=:error,
                  result_snapshot=CAST(:result_snapshot AS jsonb),
                  finished_at=now(),
                  updated_at=now()
              WHERE id=CAST(:id AS uuid)
            """),
            {
                "id":audit_id,
                "status":status,
                "phase":str(result.get("phase") or "complete"),
                "live_model_verified":bool(result.get("live_model_verified")),
                "budget_status":str(
                    result.get("budget_status") or "NOT_EVALUATED"
                ),
                "gateway_request_count":int(
                    result.get("gateway_request_count") or 0
                ),
                "prompt_tokens":int(result.get("prompt_tokens") or 0),
                "completion_tokens":int(
                    result.get("completion_tokens") or 0
                ),
                "total_tokens":int(result.get("total_tokens") or 0),
                "estimated_cost_usd":float(
                    result.get("estimated_cost_usd") or 0
                ),
                "model_request_observed":bool(
                    result.get("model_request_observed")
                ),
                "tests_passed":bool(result.get("tests_passed")),
                "artifact_count":int(result.get("artifact_count") or 0),
                "source_tree_sha256":result.get("source_tree_sha256"),
                "error":result.get("error"),
                "result_snapshot":json.dumps(
                    result,ensure_ascii=False,sort_keys=True,default=str
                ),
            },
        )


def duplicate_response(row: dict[str, Any]) -> dict[str, Any]:
    snapshot=row.get("result_snapshot")
    if row.get("acceptance_status") in {"PASSED","FAILED"} and isinstance(
        snapshot,dict
    ) and snapshot:
        result=dict(snapshot)
        result["audit_id"]=row["id"]
        result["duplicate_suppressed"]=True
        result["duplicate_requests"]=int(row.get("duplicate_requests") or 0)
        return result
    return {
        "acceptance_status":"IN_PROGRESS",
        "phase":row.get("phase") or "claimed",
        "provider":row.get("provider") or "AIHUBMIX",
        "model":row.get("model"),
        "gateway_mode":row.get("gateway_mode") or "PROXY",
        "live_model_verified":bool(row.get("live_model_verified")),
        "budget_status":row.get("budget_status") or "NOT_EVALUATED",
        "gateway_request_count":int(row.get("gateway_request_count") or 0),
        "prompt_tokens":int(row.get("prompt_tokens") or 0),
        "completion_tokens":int(row.get("completion_tokens") or 0),
        "total_tokens":int(row.get("total_tokens") or 0),
        "estimated_cost_usd":float(row.get("estimated_cost_usd") or 0),
        "model_request_observed":bool(row.get("model_request_observed")),
        "tests_passed":bool(row.get("tests_passed")),
        "artifact_count":int(row.get("artifact_count") or 0),
        "audit_id":row["id"],
        "duplicate_suppressed":True,
        "duplicate_requests":int(row.get("duplicate_requests") or 0),
    }
