from __future__ import annotations

import hashlib
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
    for key in ("id","account_id","source_scorecard_id"):
        if out.get(key) is not None:
            out[key]=str(out[key])
    for key in (
        "bucket_start","bucket_end","generated_at","evaluated_at",
        "baseline_window_start","baseline_window_end",
        "current_window_start","current_window_end",
        "detected_at","resolved_at","superseded_at",
    ):
        if out.get(key) is not None:
            out[key]=out[key].isoformat()
    return out


def _success_rate(success:int, breach:int)->float:
    total=success+breach
    return round((success*100.0/total),2) if total else 100.0


def refresh_trend_points(*,actor:str)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT s.*,a.account_key
          FROM shrimp_bilibili_reliability_scorecards s
          LEFT JOIN shrimp_bilibili_accounts a ON a.id=s.account_id
          ORDER BY s.generated_at,s.id
        """)).mappings().all()
    results=[]
    for raw in rows:
        s=dict(raw)
        payload=s["metrics_payload"] or {}
        ack=payload.get("ack_slo") or {}
        recovery=payload.get("recovery_slo") or {}
        for bucket_type in ("DAILY","WEEKLY"):
            generated=s["generated_at"].astimezone(timezone.utc)
            if bucket_type=="DAILY":
                start=generated.replace(hour=0,minute=0,second=0,microsecond=0)
                end=start+timedelta(days=1)
            else:
                start=(generated-timedelta(days=generated.weekday())).replace(
                    hour=0,minute=0,second=0,microsecond=0
                )
                end=start+timedelta(days=7)
            trend={
                "scope_type":s["scope_type"],
                "account_key":s.get("account_key"),
                "bucket_type":bucket_type,
                "bucket_start":start.isoformat(),
                "bucket_end":end.isoformat(),
                "source_scorecard_id":str(s["id"]),
                "reliability_score":float(s["reliability_score"]),
                "ack_success_rate":float(ack.get("success_rate",100.0)),
                "recovery_success_rate":float(recovery.get("success_rate",100.0)),
                "ambiguity_rate_percent":float(s["ambiguity_rate_percent"]),
                "circuit_open_count":int(s["circuit_open_count"]),
                "recurring_root_cause_count":int(s["recurring_root_cause_count"]),
            }
            sha=_sha(trend)
            with engine.begin() as db:
                row=db.execute(text("""
                  INSERT INTO shrimp_bilibili_reliability_trend_points(
                    scope_type,account_id,bucket_type,bucket_start,bucket_end,
                    source_scorecard_id,reliability_score,ack_success_rate,
                    recovery_success_rate,ambiguity_rate_percent,
                    circuit_open_count,recurring_root_cause_count,
                    trend_payload,trend_sha256,generated_by)
                  VALUES(
                    :scope_type,:account_id,:bucket_type,:bucket_start,:bucket_end,
                    :scorecard_id,:score,:ack_rate,:recovery_rate,:ambiguity,
                    :circuits,:recurrence,CAST(:payload AS jsonb),:sha,:actor)
                  ON CONFLICT (trend_sha256) DO NOTHING
                  RETURNING *
                """),{
                    "scope_type":s["scope_type"],
                    "account_id":s["account_id"],
                    "bucket_type":bucket_type,
                    "bucket_start":start,
                    "bucket_end":end,
                    "scorecard_id":s["id"],
                    "score":trend["reliability_score"],
                    "ack_rate":trend["ack_success_rate"],
                    "recovery_rate":trend["recovery_success_rate"],
                    "ambiguity":trend["ambiguity_rate_percent"],
                    "circuits":trend["circuit_open_count"],
                    "recurrence":trend["recurring_root_cause_count"],
                    "payload":canonical_json(trend),
                    "sha":sha,
                    "actor":actor[:200],
                }).mappings().one_or_none()
                if row is None:
                    row=db.execute(text("""
                      SELECT * FROM shrimp_bilibili_reliability_trend_points
                      WHERE trend_sha256=:sha
                    """),{"sha":sha}).mappings().one()
            results.append(_ser(row))
    return results


def _failure_counts(db, *,start, end, account_id=None)->dict:
    params={"start":start,"end":end}
    account_clause=""
    if account_id is not None:
        params["account_id"]=account_id
        account_clause=" AND i.account_id=:account_id"
    row=db.execute(text(f"""
      SELECT
        COUNT(*) FILTER (
          WHERE i.ack_due_at IS NOT NULL
            AND (
              (i.acknowledged_at IS NOT NULL AND i.acknowledged_at>i.ack_due_at)
              OR (i.acknowledged_at IS NULL AND i.ack_due_at<:end)
            )
        ) AS ack_fail,
        COUNT(*) FILTER (
          WHERE i.ack_due_at IS NOT NULL
        ) AS ack_total,
        COUNT(*) FILTER (
          WHERE i.recovery_due_at IS NOT NULL
            AND (
              (i.resolved_at IS NOT NULL AND i.resolved_at>i.recovery_due_at)
              OR (i.resolved_at IS NULL AND i.recovery_due_at<:end)
            )
        ) AS recovery_fail,
        COUNT(*) FILTER (
          WHERE i.recovery_due_at IS NOT NULL
        ) AS recovery_total
      FROM shrimp_bilibili_incidents i
      WHERE i.opened_at>=:start AND i.opened_at<:end
      {account_clause}
    """),params).mappings().one()
    return {k:int(v or 0) for k,v in dict(row).items()}


def _burn(fail:int,total:int,target:float)->float:
    if total<=0:
        return 0.0
    allowed=max(0.0001,(100.0-target)/100.0)
    actual=fail/total
    return round(actual/allowed,4)


def evaluate_burn_rates(*,actor:str)->list[dict]:
    short_h=max(1,int(settings.shrimp_bilibili_burn_short_window_hours))
    long_h=max(short_h,int(settings.shrimp_bilibili_burn_long_window_hours))
    end=datetime.now(timezone.utc)
    with engine.connect() as db:
        accounts=db.execute(text("""
          SELECT id,account_key FROM shrimp_bilibili_accounts
          WHERE account_status='ACTIVE' ORDER BY account_key
        """)).mappings().all()
    scopes=[("GLOBAL",None,None)]
    scopes.extend(("ACCOUNT",x["id"],x["account_key"]) for x in accounts)
    results=[]
    for scope,account_id,account_key in scopes:
        with engine.connect() as db:
            short=_failure_counts(
                db,start=end-timedelta(hours=short_h),end=end,account_id=account_id
            )
            long=_failure_counts(
                db,start=end-timedelta(hours=long_h),end=end,account_id=account_id
            )
        ack_short=_burn(short["ack_fail"],short["ack_total"],float(settings.shrimp_bilibili_ack_slo_target_percent))
        ack_long=_burn(long["ack_fail"],long["ack_total"],float(settings.shrimp_bilibili_ack_slo_target_percent))
        rec_short=_burn(short["recovery_fail"],short["recovery_total"],float(settings.shrimp_bilibili_recovery_slo_target_percent))
        rec_long=_burn(long["recovery_fail"],long["recovery_total"],float(settings.shrimp_bilibili_recovery_slo_target_percent))
        peak=max(ack_short,ack_long,rec_short,rec_long)
        if peak>=max(1.0,float(settings.shrimp_bilibili_burn_fast_threshold)):
            status="FAST_BURN"
        elif peak>=max(0.1,float(settings.shrimp_bilibili_burn_watch_threshold)):
            status="WATCH"
        else:
            status="HEALTHY"
        evidence={
            "scope_type":scope,
            "account_key":account_key,
            "evaluated_at":end.isoformat(),
            "short_window_hours":short_h,
            "long_window_hours":long_h,
            "ack_short_burn_rate":ack_short,
            "ack_long_burn_rate":ack_long,
            "recovery_short_burn_rate":rec_short,
            "recovery_long_burn_rate":rec_long,
            "burn_status":status,
            "short_counts":short,
            "long_counts":long,
        }
        sha=_sha(evidence)
        with engine.begin() as db:
            row=db.execute(text("""
              INSERT INTO shrimp_bilibili_error_budget_burn_evaluations(
                scope_type,account_id,evaluated_at,
                short_window_hours,long_window_hours,
                ack_short_burn_rate,ack_long_burn_rate,
                recovery_short_burn_rate,recovery_long_burn_rate,
                burn_status,evidence_payload,evidence_sha256,generated_by)
              VALUES(
                :scope,:account_id,:evaluated_at,:short_h,:long_h,
                :ack_short,:ack_long,:rec_short,:rec_long,:status,
                CAST(:payload AS jsonb),:sha,:actor)
              ON CONFLICT (evidence_sha256) DO NOTHING
              RETURNING *
            """),{
                "scope":scope,"account_id":account_id,"evaluated_at":end,
                "short_h":short_h,"long_h":long_h,
                "ack_short":ack_short,"ack_long":ack_long,
                "rec_short":rec_short,"rec_long":rec_long,
                "status":status,"payload":canonical_json(evidence),
                "sha":sha,"actor":actor[:200],
            }).mappings().one_or_none()
            if row is None:
                row=db.execute(text("""
                  SELECT * FROM shrimp_bilibili_error_budget_burn_evaluations
                  WHERE evidence_sha256=:sha
                """),{"sha":sha}).mappings().one()
        results.append(_ser(row))
    return results


def _latest_scope_points(db,scope_type,account_id):
    params={"scope":scope_type}
    clause="account_id IS NULL"
    if account_id is not None:
        params["account_id"]=account_id
        clause="account_id=:account_id"
    return db.execute(text(f"""
      SELECT DISTINCT ON (bucket_start) *
      FROM shrimp_bilibili_reliability_trend_points
      WHERE scope_type=:scope AND {clause} AND bucket_type='DAILY'
      ORDER BY bucket_start DESC,generated_at DESC
      LIMIT 14
    """),params).mappings().all()


def detect_regressions(*,actor:str)->list[dict]:
    with engine.connect() as db:
        accounts=db.execute(text("""
          SELECT id,account_key FROM shrimp_bilibili_accounts
          WHERE account_status='ACTIVE' ORDER BY account_key
        """)).mappings().all()
    scopes=[("GLOBAL",None,None)]
    scopes.extend(("ACCOUNT",x["id"],x["account_key"]) for x in accounts)
    created=[]
    for scope,account_id,account_key in scopes:
        with engine.connect() as db:
            points=list(_latest_scope_points(db,scope,account_id))
        if len(points)<2:
            continue
        current=dict(points[0])
        baseline=dict(points[1])
        metrics=[
          ("RELIABILITY_SCORE","reliability_score","drop",float(settings.shrimp_bilibili_regression_score_drop_points)),
          ("ACK_SUCCESS_RATE","ack_success_rate","drop",float(settings.shrimp_bilibili_regression_rate_drop_percent)),
          ("RECOVERY_SUCCESS_RATE","recovery_success_rate","drop",float(settings.shrimp_bilibili_regression_rate_drop_percent)),
          ("AMBIGUITY_RATE","ambiguity_rate_percent","rise",float(settings.shrimp_bilibili_regression_ambiguity_increase_percent)),
          ("CIRCUIT_OPEN_COUNT","circuit_open_count","rise",1.0),
          ("RECURRENCE_COUNT","recurring_root_cause_count","rise",1.0),
        ]
        for metric_key,column,direction,threshold in metrics:
            base=float(baseline[column] or 0)
            cur=float(current[column] or 0)
            delta=cur-base
            reg=(direction=="drop" and delta<=-threshold) or (direction=="rise" and delta>=threshold)
            if not reg:
                continue
            rel=(delta/abs(base)*100.0) if base else (100.0 if delta else 0.0)
            severity="CRITICAL" if abs(delta)>=threshold*2 else "WARNING"
            evidence={
                "scope_type":scope,"account_key":account_key,
                "metric_key":metric_key,"baseline_value":base,"current_value":cur,
                "absolute_delta":round(delta,4),"relative_delta_percent":round(rel,4),
                "baseline_window_start":baseline["bucket_start"].isoformat(),
                "baseline_window_end":baseline["bucket_end"].isoformat(),
                "current_window_start":current["bucket_start"].isoformat(),
                "current_window_end":current["bucket_end"].isoformat(),
                "severity":severity,
            }
            sha=_sha(evidence)
            with engine.begin() as db:
                row=db.execute(text("""
                  INSERT INTO shrimp_bilibili_reliability_regressions(
                    scope_type,account_id,metric_key,regression_status,severity,
                    baseline_value,current_value,absolute_delta,relative_delta_percent,
                    baseline_window_start,baseline_window_end,
                    current_window_start,current_window_end,
                    evidence_payload,evidence_sha256,detected_by)
                  VALUES(
                    :scope,:account_id,:metric,'OPEN',:severity,
                    :baseline,:current,:delta,:relative,
                    :baseline_start,:baseline_end,:current_start,:current_end,
                    CAST(:payload AS jsonb),:sha,:actor)
                  ON CONFLICT (evidence_sha256) DO NOTHING
                  RETURNING *
                """),{
                    "scope":scope,"account_id":account_id,"metric":metric_key,
                    "severity":severity,"baseline":base,"current":cur,
                    "delta":round(delta,4),"relative":round(rel,4),
                    "baseline_start":baseline["bucket_start"],
                    "baseline_end":baseline["bucket_end"],
                    "current_start":current["bucket_start"],
                    "current_end":current["bucket_end"],
                    "payload":canonical_json(evidence),"sha":sha,"actor":actor[:200],
                }).mappings().one_or_none()
                if row is not None:
                    created.append(_ser(row))
    return created


def _recommendation_rules(latest_scorecard,burn,regressions,recurrences):
    recs=[]
    if latest_scorecard:
        payload=latest_scorecard.get("metrics_payload") or {}
        ack=(payload.get("ack_slo") or {})
        recovery=(payload.get("recovery_slo") or {})
        if float(ack.get("success_rate",100))<float(settings.shrimp_bilibili_ack_slo_target_percent):
            recs.append(("ACK_PROCESS","HIGH","Improve incident acknowledgement workflow","Review on-call coverage and ACK ownership handoff; reduce acknowledgement latency."))
        if float(recovery.get("success_rate",100))<float(settings.shrimp_bilibili_recovery_slo_target_percent):
            recs.append(("RECOVERY_PROCESS","HIGH","Review recovery path latency","Inspect evidence collection and approval handoffs contributing to MTTR and Recovery SLO breaches."))
        if float(latest_scorecard.get("ambiguity_rate_percent") or 0)>float(settings.shrimp_bilibili_ambiguity_target_percent):
            recs.append(("PROVIDER_AMBIGUITY","HIGH","Reduce provider ambiguity","Audit upload/publish read-back identifiers and reconciliation evidence quality before changing retry policy."))
        if int(latest_scorecard.get("circuit_open_count") or 0)>0:
            recs.append(("CIRCUIT_STABILITY","MEDIUM","Review repeated Circuit openings","Compare circuit-open events with credential health and provider-failure sequences."))
    if burn and burn.get("burn_status") in {"FAST_BURN","EXHAUSTED"}:
        recs.append(("ERROR_BUDGET","CRITICAL","Error budget is burning too quickly","Prioritize reliability work and avoid increasing automated publishing exposure until burn rate returns to healthy range."))
    if any(x.get("recurrence_status")=="RECURRING" for x in recurrences):
        recs.append(("ROOT_CAUSE_RECURRENCE","HIGH","Repeated root cause detected","Review PIR corrective actions for recurring clusters and verify completion evidence addresses the repeated mechanism."))
    if regressions:
        recs.append(("ERROR_BUDGET","MEDIUM","Reliability regression detected","Compare current and previous reliability windows before changing operational thresholds."))
    return recs


def generate_policy_recommendations(*,actor:str)->list[dict]:
    dashboard=trend_dashboard()
    latest=dashboard.get("latest_global_scorecard")
    burn=dashboard.get("latest_global_burn")
    regressions=dashboard.get("open_regressions") or []
    recurrences=dashboard.get("recurrence_clusters") or []
    result=[]
    for category,priority,title,text_value in _recommendation_rules(latest,burn,regressions,recurrences):
        evidence={
            "category":category,"priority":priority,
            "latest_scorecard_sha256":latest.get("scorecard_sha256") if latest else None,
            "burn_evidence_sha256":burn.get("evidence_sha256") if burn else None,
            "regression_ids":[x["id"] for x in regressions],
            "recurrence_fingerprints":[x["root_cause_fingerprint"] for x in recurrences if x.get("recurrence_status")=="RECURRING"],
        }
        recommendation={
            "recommendation_key":category.lower(),
            "priority":priority,"category":category,"title":title,
            "recommendation_text":text_value,
            "rationale":"Generated from deterministic reliability rules and current persisted evidence.",
            "evidence":evidence,
        }
        rec_sha=_sha(recommendation)
        with engine.begin() as db:
            row=db.execute(text("""
              INSERT INTO shrimp_bilibili_reliability_policy_recommendations(
                scope_type,account_id,recommendation_key,recommendation_status,
                priority,category,title,recommendation_text,rationale,
                evidence_payload,evidence_sha256,recommendation_sha256,generated_by)
              VALUES(
                'GLOBAL',NULL,:key,'OPEN',:priority,:category,:title,:text,:rationale,
                CAST(:evidence AS jsonb),:evidence_sha,:rec_sha,:actor)
              ON CONFLICT (recommendation_sha256) DO NOTHING
              RETURNING *
            """),{
                "key":recommendation["recommendation_key"],
                "priority":priority,"category":category,"title":title,
                "text":text_value,"rationale":recommendation["rationale"],
                "evidence":canonical_json(evidence),"evidence_sha":_sha(evidence),
                "rec_sha":rec_sha,"actor":actor[:200],
            }).mappings().one_or_none()
            if row is not None:
                result.append(_ser(row))
    return result


def list_trend_points(*,limit:int=200)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT t.*,a.account_key
          FROM shrimp_bilibili_reliability_trend_points t
          LEFT JOIN shrimp_bilibili_accounts a ON a.id=t.account_id
          ORDER BY t.bucket_start DESC,t.generated_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),1000))}).mappings().all()
    return [_ser(x) for x in rows]


def list_burn_rates(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT b.*,a.account_key
          FROM shrimp_bilibili_error_budget_burn_evaluations b
          LEFT JOIN shrimp_bilibili_accounts a ON a.id=b.account_id
          ORDER BY b.evaluated_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def list_regressions(*,status:str|None=None,limit:int=100)->list[dict]:
    params={"limit":max(1,min(int(limit),500))}
    sql="""
      SELECT r.*,a.account_key
      FROM shrimp_bilibili_reliability_regressions r
      LEFT JOIN shrimp_bilibili_accounts a ON a.id=r.account_id
    """
    if status:
        sql+=" WHERE r.regression_status=:status"
        params["status"]=status.upper()
    sql+=" ORDER BY CASE r.severity WHEN 'CRITICAL' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,r.detected_at DESC LIMIT :limit"
    with engine.connect() as db:
        rows=db.execute(text(sql),params).mappings().all()
    return [_ser(x) for x in rows]


def list_policy_recommendations(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT p.*,a.account_key
          FROM shrimp_bilibili_reliability_policy_recommendations p
          LEFT JOIN shrimp_bilibili_accounts a ON a.id=p.account_id
          WHERE p.recommendation_status='OPEN'
          ORDER BY CASE p.priority
                     WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                     WHEN 'MEDIUM' THEN 3 ELSE 4 END,
                   p.generated_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def trend_dashboard()->dict:
    from app.providers.animation.shrimp.bilibili_reliability import (
        list_recurrence_clusters,
        list_reliability_scorecards,
    )
    scorecards=list_reliability_scorecards(limit=100)
    burns=list_burn_rates(limit=100)
    trends=list_trend_points(limit=300)
    regressions=list_regressions(status="OPEN",limit=100)
    recurrences=list_recurrence_clusters(limit=100)
    latest_global=next((x for x in scorecards if x["scope_type"]=="GLOBAL"),None)
    latest_burn=next((x for x in burns if x["scope_type"]=="GLOBAL"),None)
    return {
        "latest_global_scorecard":latest_global,
        "latest_global_burn":latest_burn,
        "trend_points":trends,
        "burn_evaluations":burns,
        "open_regressions":regressions,
        "recommendations":list_policy_recommendations(limit=100),
        "recurrence_clusters":recurrences,
        "secrets_redacted":True,
        "observe_only":True,
    }


def run_reliability_analysis(*,actor:str)->dict:
    trends=refresh_trend_points(actor=actor+"-trend")
    burns=evaluate_burn_rates(actor=actor+"-burn")
    regressions=detect_regressions(actor=actor+"-regression")
    recommendations=generate_policy_recommendations(actor=actor+"-policy")
    return {
        "trend_points":len(trends),
        "burn_evaluations":len(burns),
        "new_regressions":len(regressions),
        "new_recommendations":len(recommendations),
        "observe_only":True,
    }
