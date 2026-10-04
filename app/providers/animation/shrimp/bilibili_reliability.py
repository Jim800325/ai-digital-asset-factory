from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _ser(row: Any) -> dict:
    out=dict(row)
    for key in ("id","account_id"):
        if out.get(key) is not None:
            out[key]=str(out[key])
    for key in ("window_start","window_end","generated_at","first_observed_at","last_observed_at","latest_pir_completed_at","updated_at"):
        if out.get(key) is not None:
            out[key]=out[key].isoformat()
    return out


def _normalize_root_cause(value: str) -> str:
    text_value=unicodedata.normalize("NFKC",value or "").strip().lower()
    text_value=re.sub(r"[^\w\u4e00-\u9fff]+"," ",text_value,flags=re.UNICODE)
    return re.sub(r"\s+"," ",text_value).strip()


def refresh_recurrence_clusters(*,actor:str)->list[dict]:
    threshold=max(2,int(settings.shrimp_bilibili_recurrence_threshold))
    with engine.begin() as db:
        rows=db.execute(text("""
          SELECT p.id AS pir_id,p.incident_id,p.root_cause,p.completed_at,
                 i.severity,a.account_key,i.opened_at
          FROM shrimp_bilibili_post_incident_reviews p
          JOIN shrimp_bilibili_incidents i ON i.id=p.incident_id
          JOIN shrimp_bilibili_accounts a ON a.id=i.account_id
          WHERE p.review_status='COMPLETED'
            AND p.root_cause IS NOT NULL
            AND btrim(p.root_cause)<>''
          ORDER BY p.completed_at,i.opened_at,p.id
        """)).mappings().all()

        grouped:dict[str,dict]={}
        for row in rows:
            normalized=_normalize_root_cause(row["root_cause"])
            if not normalized:
                continue
            fp=hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            group=grouped.setdefault(fp,{
                "normalized_root_cause":normalized,
                "incident_ids":[],
                "account_keys":[],
                "critical_count":0,
                "first_observed_at":row["opened_at"],
                "last_observed_at":row["opened_at"],
                "latest_pir_completed_at":row["completed_at"],
            })
            group["incident_ids"].append(str(row["incident_id"]))
            if row["account_key"] not in group["account_keys"]:
                group["account_keys"].append(row["account_key"])
            if row["severity"]=="CRITICAL":
                group["critical_count"]+=1
            if row["opened_at"]<group["first_observed_at"]:
                group["first_observed_at"]=row["opened_at"]
            if row["opened_at"]>group["last_observed_at"]:
                group["last_observed_at"]=row["opened_at"]
            if row["completed_at"] and (
                group["latest_pir_completed_at"] is None
                or row["completed_at"]>group["latest_pir_completed_at"]
            ):
                group["latest_pir_completed_at"]=row["completed_at"]

        results=[]
        for fp,group in grouped.items():
            count=len(group["incident_ids"])
            status=(
                "RECURRING" if count>=threshold
                else "WATCH" if count==threshold-1
                else "OBSERVED"
            )
            evidence={
                "root_cause_fingerprint":fp,
                "normalized_root_cause":group["normalized_root_cause"],
                "occurrence_count":count,
                "critical_occurrence_count":group["critical_count"],
                "incident_ids":group["incident_ids"],
                "account_keys":group["account_keys"],
                "recurrence_status":status,
            }
            row=db.execute(text("""
              INSERT INTO shrimp_bilibili_recurrence_clusters(
                root_cause_fingerprint,normalized_root_cause,
                recurrence_status,occurrence_count,critical_occurrence_count,
                incident_ids,account_keys,first_observed_at,last_observed_at,
                latest_pir_completed_at,evidence_payload,evidence_sha256,
                detected_by,updated_at)
              VALUES(
                :fp,:root,:status,:count,:critical,
                CAST(:incident_ids AS jsonb),CAST(:account_keys AS jsonb),
                :first_at,:last_at,:latest_pir,
                CAST(:payload AS jsonb),:sha,:actor,now())
              ON CONFLICT (root_cause_fingerprint) DO UPDATE
              SET normalized_root_cause=EXCLUDED.normalized_root_cause,
                  recurrence_status=EXCLUDED.recurrence_status,
                  occurrence_count=EXCLUDED.occurrence_count,
                  critical_occurrence_count=EXCLUDED.critical_occurrence_count,
                  incident_ids=EXCLUDED.incident_ids,
                  account_keys=EXCLUDED.account_keys,
                  first_observed_at=EXCLUDED.first_observed_at,
                  last_observed_at=EXCLUDED.last_observed_at,
                  latest_pir_completed_at=EXCLUDED.latest_pir_completed_at,
                  evidence_payload=EXCLUDED.evidence_payload,
                  evidence_sha256=EXCLUDED.evidence_sha256,
                  detected_by=EXCLUDED.detected_by,
                  updated_at=now()
              RETURNING *
            """),{
                "fp":fp,
                "root":group["normalized_root_cause"],
                "status":status,
                "count":count,
                "critical":group["critical_count"],
                "incident_ids":canonical_json(group["incident_ids"]),
                "account_keys":canonical_json(group["account_keys"]),
                "first_at":group["first_observed_at"],
                "last_at":group["last_observed_at"],
                "latest_pir":group["latest_pir_completed_at"],
                "payload":canonical_json(evidence),
                "sha":_sha(evidence),
                "actor":actor[:200],
            }).mappings().one()
            results.append(_ser(row))
    return results


def list_recurrence_clusters(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_recurrence_clusters
          ORDER BY CASE recurrence_status
                     WHEN 'RECURRING' THEN 1
                     WHEN 'WATCH' THEN 2
                     ELSE 3
                   END,
                   occurrence_count DESC,last_observed_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def _error_budget(total:int, breach:int, target:float)->dict:
    if total<=0:
        return {"allowed":0,"consumed":0,"remaining":0,"success_rate":100.0}
    allowed=max(1,math.ceil(total*max(0.0,100.0-target)/100.0))
    success=max(0,total-breach)
    rate=round(success*100.0/total,2)
    return {
        "allowed":allowed,
        "consumed":breach,
        "remaining":max(0,allowed-breach),
        "success_rate":rate,
    }


def _grade(score:float)->str:
    if score>=95:return "A"
    if score>=85:return "B"
    if score>=75:return "C"
    if score>=60:return "D"
    return "F"


def _score(
    *,
    ack_rate:float,
    recovery_rate:float,
    ambiguity_rate:float,
    circuit_open_count:int,
    recurrence_count:int,
)->float:
    ack_target=max(1.0,float(settings.shrimp_bilibili_ack_slo_target_percent))
    rec_target=max(1.0,float(settings.shrimp_bilibili_recovery_slo_target_percent))
    amb_target=max(0.1,float(settings.shrimp_bilibili_ambiguity_target_percent))
    ack_points=min(1.0,ack_rate/ack_target)*25.0
    recovery_points=min(1.0,recovery_rate/rec_target)*35.0
    ambiguity_points=20.0 if ambiguity_rate<=amb_target else max(
        0.0,
        20.0*(1.0-(ambiguity_rate-amb_target)/max(amb_target,1.0)),
    )
    circuit_points=max(0.0,10.0-min(10.0,circuit_open_count*2.5))
    recurrence_points=max(0.0,10.0-min(10.0,recurrence_count*2.5))
    return round(ack_points+recovery_points+ambiguity_points+circuit_points+recurrence_points,2)


def _scope_metrics(db, *,window_start,window_end,account_id=None)->dict:
    account_filter=" AND i.account_id=:account_id" if account_id else ""
    params={"start":window_start,"end":window_end}
    if account_id:
        params["account_id"]=account_id

    incident=db.execute(text(f"""
      SELECT
        COUNT(*) AS incident_count,
        COUNT(*) FILTER (WHERE i.incident_status='RESOLVED') AS resolved_count,
        COUNT(*) FILTER (
          WHERE i.acknowledgement_status='ACKNOWLEDGED'
        ) AS acknowledged_count,
        COUNT(*) FILTER (
          WHERE i.acknowledged_at IS NOT NULL
            AND i.ack_due_at IS NOT NULL
            AND i.acknowledged_at<=i.ack_due_at
        ) AS ack_success,
        COUNT(*) FILTER (
          WHERE (
            i.ack_due_at IS NOT NULL
            AND (
              (i.acknowledged_at IS NOT NULL AND i.acknowledged_at>i.ack_due_at)
              OR (i.acknowledged_at IS NULL AND i.ack_due_at<:end)
            )
          )
        ) AS ack_breach,
        COUNT(*) FILTER (
          WHERE i.incident_status='RESOLVED'
            AND i.resolved_at IS NOT NULL
            AND i.recovery_due_at IS NOT NULL
            AND i.resolved_at<=i.recovery_due_at
        ) AS recovery_success,
        COUNT(*) FILTER (
          WHERE i.recovery_due_at IS NOT NULL
            AND (
              (i.resolved_at IS NOT NULL AND i.resolved_at>i.recovery_due_at)
              OR (i.resolved_at IS NULL AND i.recovery_due_at<:end)
            )
        ) AS recovery_breach,
        AVG(EXTRACT(EPOCH FROM (i.acknowledged_at-i.opened_at))/60.0)
          FILTER (WHERE i.acknowledged_at IS NOT NULL) AS avg_ack,
        percentile_cont(0.95) WITHIN GROUP (
          ORDER BY EXTRACT(EPOCH FROM (i.acknowledged_at-i.opened_at))/60.0
        ) FILTER (WHERE i.acknowledged_at IS NOT NULL) AS p95_ack,
        AVG(EXTRACT(EPOCH FROM (i.resolved_at-i.opened_at))/60.0)
          FILTER (WHERE i.resolved_at IS NOT NULL) AS avg_mttr,
        percentile_cont(0.95) WITHIN GROUP (
          ORDER BY EXTRACT(EPOCH FROM (i.resolved_at-i.opened_at))/60.0
        ) FILTER (WHERE i.resolved_at IS NOT NULL) AS p95_mttr
      FROM shrimp_bilibili_incidents i
      WHERE i.opened_at>=:start AND i.opened_at<:end
      {account_filter}
    """),params).mappings().one()

    circuit_filter=" AND e.account_id=:account_id" if account_id else ""
    circuit_count=int(db.execute(text(f"""
      SELECT COUNT(*)
      FROM shrimp_bilibili_circuit_events e
      WHERE e.event_type IN ('AUTO_OPEN_AMBIGUITY','AUTO_OPEN_PROVIDER_FAILURE','MANUAL_OPEN')
        AND e.created_at>=:start AND e.created_at<:end
      {circuit_filter}
    """),params).scalar_one())

    ambiguity_filter=" AND r.account_id=:account_id" if account_id else ""
    ambiguity_count=int(db.execute(text(f"""
      SELECT COUNT(*)
      FROM shrimp_bilibili_stuck_claim_reconciliations r
      WHERE r.reconciliation_outcome='STILL_AMBIGUOUS'
        AND r.reconciled_at>=:start AND r.reconciled_at<:end
      {ambiguity_filter}
    """),params).scalar_one())

    exec_filter=" AND c.account_id=:account_id" if account_id else ""
    execution_count=int(db.execute(text(f"""
      SELECT COUNT(*)
      FROM shrimp_animation_publish_executions e
      LEFT JOIN shrimp_bilibili_execution_claims c ON c.execution_id=e.id
      WHERE e.created_at>=:start AND e.created_at<:end
      {exec_filter}
    """),params).scalar_one())

    recurrence_count=int(db.execute(text("""
      SELECT COUNT(*)
      FROM shrimp_bilibili_recurrence_clusters
      WHERE recurrence_status='RECURRING'
        AND last_observed_at>=:start
        AND last_observed_at<:end
    """),params).scalar_one())

    return {
        "incident":dict(incident),
        "circuit_open_count":circuit_count,
        "provider_ambiguity_count":ambiguity_count,
        "publisher_execution_count":execution_count,
        "recurring_root_cause_count":recurrence_count,
    }


def generate_reliability_scorecards(*,actor:str)->list[dict]:
    refresh_recurrence_clusters(actor=actor+"-recurrence")
    days=max(1,int(settings.shrimp_bilibili_reliability_window_days))
    end=datetime.now(timezone.utc)
    start=end-timedelta(days=days)
    with engine.connect() as db:
        accounts=db.execute(text("""
          SELECT id,account_key
          FROM shrimp_bilibili_accounts
          WHERE account_status='ACTIVE'
          ORDER BY account_key
        """)).mappings().all()

    scopes=[("GLOBAL",None,None)]
    scopes.extend(("ACCOUNT",x["id"],x["account_key"]) for x in accounts)
    results=[]
    for scope_type,account_id,account_key in scopes:
        with engine.connect() as db:
            m=_scope_metrics(
                db,
                window_start=start,
                window_end=end,
                account_id=account_id,
            )
        inc=m["incident"]
        ack_total=int(inc["ack_success"] or 0)+int(inc["ack_breach"] or 0)
        recovery_total=int(inc["recovery_success"] or 0)+int(inc["recovery_breach"] or 0)
        ack_budget=_error_budget(
            ack_total,
            int(inc["ack_breach"] or 0),
            float(settings.shrimp_bilibili_ack_slo_target_percent),
        )
        recovery_budget=_error_budget(
            recovery_total,
            int(inc["recovery_breach"] or 0),
            float(settings.shrimp_bilibili_recovery_slo_target_percent),
        )
        execution_count=m["publisher_execution_count"]
        ambiguity_rate=(
            round(m["provider_ambiguity_count"]*100.0/execution_count,3)
            if execution_count else 0.0
        )
        score=_score(
            ack_rate=ack_budget["success_rate"],
            recovery_rate=recovery_budget["success_rate"],
            ambiguity_rate=ambiguity_rate,
            circuit_open_count=m["circuit_open_count"],
            recurrence_count=m["recurring_root_cause_count"],
        )
        payload={
            "scope_type":scope_type,
            "account_key":account_key,
            "window_start":start.isoformat(),
            "window_end":end.isoformat(),
            "window_days":days,
            "incident_count":int(inc["incident_count"] or 0),
            "resolved_incident_count":int(inc["resolved_count"] or 0),
            "acknowledged_incident_count":int(inc["acknowledged_count"] or 0),
            "ack_slo":{"target_percent":float(settings.shrimp_bilibili_ack_slo_target_percent),**ack_budget},
            "recovery_slo":{"target_percent":float(settings.shrimp_bilibili_recovery_slo_target_percent),**recovery_budget},
            "avg_ack_minutes":round(float(inc["avg_ack"]),2) if inc["avg_ack"] is not None else None,
            "p95_ack_minutes":round(float(inc["p95_ack"]),2) if inc["p95_ack"] is not None else None,
            "avg_mttr_minutes":round(float(inc["avg_mttr"]),2) if inc["avg_mttr"] is not None else None,
            "p95_mttr_minutes":round(float(inc["p95_mttr"]),2) if inc["p95_mttr"] is not None else None,
            "circuit_open_count":m["circuit_open_count"],
            "provider_ambiguity_count":m["provider_ambiguity_count"],
            "publisher_execution_count":execution_count,
            "ambiguity_rate_percent":ambiguity_rate,
            "recurring_root_cause_count":m["recurring_root_cause_count"],
            "reliability_score":score,
            "reliability_grade":_grade(score),
        }
        sha=_sha(payload)
        with engine.begin() as db:
            row=db.execute(text("""
              INSERT INTO shrimp_bilibili_reliability_scorecards(
                scope_type,account_id,window_start,window_end,window_days,
                incident_count,resolved_incident_count,acknowledged_incident_count,
                ack_slo_success_count,ack_slo_breach_count,
                recovery_slo_success_count,recovery_slo_breach_count,
                avg_ack_minutes,p95_ack_minutes,avg_mttr_minutes,p95_mttr_minutes,
                circuit_open_count,provider_ambiguity_count,
                publisher_execution_count,ambiguity_rate_percent,
                recurring_root_cause_count,
                ack_slo_target_percent,recovery_slo_target_percent,
                ack_error_budget_allowed,ack_error_budget_consumed,
                ack_error_budget_remaining,recovery_error_budget_allowed,
                recovery_error_budget_consumed,recovery_error_budget_remaining,
                reliability_score,reliability_grade,metrics_payload,
                scorecard_sha256,generated_by)
              VALUES(
                :scope_type,:account_id,:start,:end,:days,
                :incidents,:resolved,:acknowledged,
                :ack_success,:ack_breach,:recovery_success,:recovery_breach,
                :avg_ack,:p95_ack,:avg_mttr,:p95_mttr,
                :circuits,:ambiguity,:executions,:ambiguity_rate,:recurrence,
                :ack_target,:recovery_target,
                :ack_allowed,:ack_consumed,:ack_remaining,
                :recovery_allowed,:recovery_consumed,:recovery_remaining,
                :score,:grade,CAST(:payload AS jsonb),:sha,:actor)
              ON CONFLICT (scorecard_sha256) DO NOTHING
              RETURNING *
            """),{
                "scope_type":scope_type,"account_id":account_id,
                "start":start,"end":end,"days":days,
                "incidents":payload["incident_count"],
                "resolved":payload["resolved_incident_count"],
                "acknowledged":payload["acknowledged_incident_count"],
                "ack_success":int(inc["ack_success"] or 0),
                "ack_breach":int(inc["ack_breach"] or 0),
                "recovery_success":int(inc["recovery_success"] or 0),
                "recovery_breach":int(inc["recovery_breach"] or 0),
                "avg_ack":payload["avg_ack_minutes"],
                "p95_ack":payload["p95_ack_minutes"],
                "avg_mttr":payload["avg_mttr_minutes"],
                "p95_mttr":payload["p95_mttr_minutes"],
                "circuits":m["circuit_open_count"],
                "ambiguity":m["provider_ambiguity_count"],
                "executions":execution_count,
                "ambiguity_rate":ambiguity_rate,
                "recurrence":m["recurring_root_cause_count"],
                "ack_target":float(settings.shrimp_bilibili_ack_slo_target_percent),
                "recovery_target":float(settings.shrimp_bilibili_recovery_slo_target_percent),
                "ack_allowed":ack_budget["allowed"],
                "ack_consumed":ack_budget["consumed"],
                "ack_remaining":ack_budget["remaining"],
                "recovery_allowed":recovery_budget["allowed"],
                "recovery_consumed":recovery_budget["consumed"],
                "recovery_remaining":recovery_budget["remaining"],
                "score":score,"grade":payload["reliability_grade"],
                "payload":canonical_json(payload),"sha":sha,
                "actor":actor[:200],
            }).mappings().one_or_none()
            if row is None:
                row=db.execute(text("""
                  SELECT * FROM shrimp_bilibili_reliability_scorecards
                  WHERE scorecard_sha256=:sha
                """),{"sha":sha}).mappings().one()
        results.append(_ser(row))
    return results


def list_reliability_scorecards(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT s.*,a.account_key
          FROM shrimp_bilibili_reliability_scorecards s
          LEFT JOIN shrimp_bilibili_accounts a ON a.id=s.account_id
          ORDER BY s.generated_at DESC,
                   CASE s.scope_type WHEN 'GLOBAL' THEN 1 ELSE 2 END,
                   a.account_key
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def reliability_dashboard()->dict:
    scorecards=list_reliability_scorecards(limit=100)
    latest_global=next((x for x in scorecards if x["scope_type"]=="GLOBAL"),None)
    recurrences=list_recurrence_clusters(limit=100)
    return {
        "latest_global":latest_global,
        "scorecards":scorecards,
        "recurrence_clusters":recurrences,
        "recurring_count":sum(1 for x in recurrences if x["recurrence_status"]=="RECURRING"),
        "watch_count":sum(1 for x in recurrences if x["recurrence_status"]=="WATCH"),
        "secrets_redacted":True,
    }
