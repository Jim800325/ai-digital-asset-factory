from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text

from app.build_proposals import ensure_build_proposal
from app.classifier import classify
from app.config import settings
from app.db import engine
from app.evidence_expansion import _mapping_decision, _targeted_ingest
from app.evidence_quality import evidence_metrics, normalized_domain
from app.providers.evidence_community import search_community
from app.validation import validation_signals
from app.validation_search_plan import (
    build_validation_search_plan,
    useful_validation_dimensions,
)


def _candidate_metrics(candidate: dict, mapping_score: float) -> dict:
    title=candidate["title"]
    content=(candidate.get("text") or "").strip()
    _,hits,pain=classify(title,content)
    confidence=min(.95,.55+float(mapping_score)*.35)
    source_class,source_quality,signal_strength=evidence_metrics(
        source_type=candidate.get("source_type") or "VALIDATION_EXPANSION",
        url=candidate["url"],
        hits=hits,
        pain=pain,
        confidence=confidence,
    )
    return {
        "source_class":source_class,
        "source_quality":round(float(source_quality),2),
        "signal_strength":round(float(signal_strength),2),
        "confidence":round(float(confidence),4),
    }


def _validation_snapshot(opportunity_id: UUID) -> dict:
    plan=build_validation_search_plan(opportunity_id)
    return {
        "opportunity":plan["opportunity"],
        "validation":plan["validation"],
        "eligibility":plan["eligibility"],
        "task_dimensions":[task["dimension"] for task in plan["tasks"]],
    }


def _create_run(opportunity_id: UUID, plan: dict, requested_by: str) -> UUID:
    initial=_validation_snapshot(opportunity_id)
    with engine.begin() as db:
        return db.execute(text("""
          INSERT INTO validation_expansion_runs(
            opportunity_id,plan_version,run_status,requested_by,search_seed,initial_validation)
          VALUES(
            :opportunity_id,:plan_version,'RUNNING',:requested_by,:search_seed,
            CAST(:initial_validation AS jsonb))
          RETURNING id
        """),{
            "opportunity_id":opportunity_id,
            "plan_version":plan["version"],
            "requested_by":requested_by[:200],
            "search_seed":plan["search_seed"],
            "initial_validation":json.dumps(initial,sort_keys=True),
        }).scalar_one()


def _existing_evidence_urls(opportunity_id: UUID) -> set[str]:
    with engine.connect() as db:
        return {
            str(row[0])
            for row in db.execute(text("""
              SELECT e.source_url
              FROM opportunity_evidence oe
              JOIN evidence e ON e.id=oe.evidence_id
              WHERE oe.opportunity_id=:id
            """),{"id":opportunity_id}).all()
            if row[0]
        }


def _record_result(
    *,
    run_id: UUID,
    task_id: str,
    provider: str,
    query: str,
    candidate: dict,
    mapping: dict,
    signals: dict,
    useful_dimensions: list[str],
    decision: str,
    reason: str,
    ingested: bool = False,
    evidence_id: str | None = None,
    snapshot: dict | None = None,
) -> None:
    with engine.begin() as db:
        db.execute(text("""
          INSERT INTO validation_expansion_results(
            run_id,task_id,provider,query,source_url,source_domain,source_class,
            title,mapping_score,matched_dimensions,useful_dimensions,
            decision,decision_reason,ingested,evidence_id,result_snapshot)
          VALUES(
            :run_id,:task_id,:provider,:query,:source_url,:source_domain,:source_class,
            :title,:mapping_score,CAST(:matched AS jsonb),CAST(:useful AS jsonb),
            :decision,:reason,:ingested,:evidence_id,CAST(:snapshot AS jsonb))
          ON CONFLICT(run_id,source_url) DO NOTHING
        """),{
            "run_id":run_id,
            "task_id":task_id,
            "provider":provider,
            "query":query[:300],
            "source_url":candidate["url"],
            "source_domain":normalized_domain(candidate["url"]),
            "source_class":candidate.get("source_class") or "community",
            "title":candidate["title"][:1000],
            "mapping_score":mapping["score"],
            "matched":json.dumps(signals,sort_keys=True),
            "useful":json.dumps(useful_dimensions),
            "decision":decision,
            "reason":reason[:1000],
            "ingested":ingested,
            "evidence_id":evidence_id,
            "snapshot":json.dumps(snapshot or {},sort_keys=True),
        })


def _finish_run(
    run_id: UUID,
    *,
    status: str,
    opportunity_id: UUID,
    counts: dict,
    error: str | None = None,
) -> dict:
    final=_validation_snapshot(opportunity_id)
    proposal=None
    if (
        final["opportunity"]["build_readiness"]=="BUILD_READY"
        and final["validation"]["validation_gate_passed"]
    ):
        proposal=ensure_build_proposal(opportunity_id)

    with engine.begin() as db:
        db.execute(text("""
          UPDATE validation_expansion_runs
          SET run_status=:status,
              queries_attempted=:queries,
              candidates_found=:candidates,
              accepted_count=:accepted,
              rejected_count=:rejected,
              ambiguous_count=:ambiguous,
              final_validation=CAST(:final_validation AS jsonb),
              error=:error,
              finished_at=now(),
              updated_at=now()
          WHERE id=:id
        """),{
            "status":status,
            "queries":counts["queries"],
            "candidates":counts["candidates"],
            "accepted":counts["accepted"],
            "rejected":counts["rejected"],
            "ambiguous":counts["ambiguous"],
            "final_validation":json.dumps(final,sort_keys=True),
            "error":error[:4000] if error else None,
            "id":run_id,
        })

    return {
        "run_id":str(run_id),
        "status":status,
        "counts":counts,
        "final":final,
        "build_proposal":proposal,
    }


def execute_validation_expansion(
    opportunity_id: str,
    *,
    requested_by: str = "controlled-api",
) -> dict:
    if not settings.validation_expansion_enabled:
        raise RuntimeError("Validation expansion execution is disabled")

    target_id=UUID(str(opportunity_id))
    plan=build_validation_search_plan(target_id)
    if not plan["eligibility"]["recommended_for_expansion"]:
        raise RuntimeError(
            "Opportunity is not eligible for validation expansion: "
            + ",".join(plan["eligibility"]["reasons"])
        )

    run_id=_create_run(target_id,plan,requested_by)
    counts={"queries":0,"candidates":0,"accepted":0,"rejected":0,"ambiguous":0}
    max_queries=min(max(int(settings.validation_expansion_max_queries),1),12)
    per_query=min(max(int(settings.validation_expansion_results_per_query),1),10)
    max_accepted=min(max(int(settings.validation_expansion_max_accepted),1),5)
    seen_urls=_existing_evidence_urls(target_id)
    seen_queries=set()

    try:
        while counts["queries"]<max_queries and counts["accepted"]<max_accepted:
            plan=build_validation_search_plan(target_id)
            if plan["validation"]["validation_gate_passed"]:
                return _finish_run(
                    run_id,status="SUCCEEDED",opportunity_id=target_id,counts=counts
                )
            tasks=plan["tasks"]
            if not tasks:
                break

            accepted_this_round=False
            any_query=False
            for task in tasks:
                for query in task["queries"]:
                    if counts["queries"]>=max_queries or counts["accepted"]>=max_accepted:
                        break
                    query_key=(task["dimension"],query)
                    if query_key in seen_queries:
                        continue
                    seen_queries.add(query_key)
                    any_query=True
                    counts["queries"]+=1
                    candidates,provider_errors=search_community(query,per_query)
                    if provider_errors:
                        print(
                            "validation expansion provider warnings: "
                            + ",".join(x["provider"] for x in provider_errors),
                            flush=True,
                        )

                    for candidate in candidates:
                        url=candidate["url"]
                        if url in seen_urls:
                            continue
                        counts["candidates"]+=1
                        domain=normalized_domain(url)
                        material=(candidate["title"]+" "+(candidate.get("text") or ""))[:12000]
                        signals=validation_signals(material)
                        mapping=_mapping_decision(
                            search_seed=plan["search_seed"],
                            opportunity_title=plan["opportunity"]["title"],
                            candidate_title=candidate["title"],
                            candidate_text=candidate.get("text") or "",
                        )
                        useful=useful_validation_dimensions(plan,domain,signals)

                        if mapping["decision"]=="AMBIGUOUS":
                            counts["ambiguous"]+=1
                            _record_result(
                                run_id=run_id,task_id=task["task_id"],
                                provider=candidate["provider"],query=query,candidate=candidate,
                                mapping=mapping,signals=signals,useful_dimensions=useful,
                                decision="AMBIGUOUS",reason=mapping["reason"],
                            )
                            continue
                        if mapping["decision"]!="ACCEPTED":
                            counts["rejected"]+=1
                            _record_result(
                                run_id=run_id,task_id=task["task_id"],
                                provider=candidate["provider"],query=query,candidate=candidate,
                                mapping=mapping,signals=signals,useful_dimensions=useful,
                                decision="REJECTED",reason=mapping["reason"],
                            )
                            continue
                        if not useful:
                            counts["rejected"]+=1
                            _record_result(
                                run_id=run_id,task_id=task["task_id"],
                                provider=candidate["provider"],query=query,candidate=candidate,
                                mapping=mapping,signals=signals,useful_dimensions=[],
                                decision="REJECTED",
                                reason="candidate does not add an independent domain to a current validation gap",
                            )
                            continue

                        metrics=_candidate_metrics(candidate,mapping["score"])
                        if metrics["source_quality"]<50 or metrics["signal_strength"]<45:
                            counts["rejected"]+=1
                            _record_result(
                                run_id=run_id,task_id=task["task_id"],
                                provider=candidate["provider"],query=query,candidate=candidate,
                                mapping=mapping,signals=signals,useful_dimensions=useful,
                                decision="REJECTED",
                                reason="candidate is below deterministic validation quality/signal thresholds",
                                snapshot={"metrics":metrics},
                            )
                            continue

                        ingest=_targeted_ingest(target_id,candidate,mapping["score"])
                        seen_urls.add(url)
                        counts["accepted"]+=1
                        accepted_this_round=True
                        target_id=UUID(ingest["aggregation"]["opportunity_id"])
                        after=build_validation_search_plan(target_id)
                        _record_result(
                            run_id=run_id,task_id=task["task_id"],
                            provider=candidate["provider"],query=query,candidate=candidate,
                            mapping=mapping,signals=signals,useful_dimensions=useful,
                            decision="ACCEPTED",
                            reason="same-Opportunity mapping and validation-dimension gate passed",
                            ingested=True,evidence_id=ingest["evidence_id"],
                            snapshot={"metrics":metrics,"ingest":ingest,"after":after["validation"]},
                        )
                        if after["validation"]["validation_gate_passed"]:
                            return _finish_run(
                                run_id,status="SUCCEEDED",opportunity_id=target_id,counts=counts
                            )
                        break

                    if accepted_this_round:
                        break
                if accepted_this_round:
                    break

            if not any_query or not accepted_this_round:
                break

        final=build_validation_search_plan(target_id)
        status="SUCCEEDED" if final["validation"]["validation_gate_passed"] else (
            "SUCCEEDED" if counts["accepted"] else "NO_MATCH"
        )
        return _finish_run(
            run_id,status=status,opportunity_id=target_id,counts=counts
        )
    except Exception as exc:
        try:
            _finish_run(
                run_id,status="FAILED",opportunity_id=target_id,counts=counts,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            raise


def get_validation_expansion_run(run_id: UUID) -> dict:
    with engine.connect() as db:
        run=db.execute(text("""
          SELECT * FROM validation_expansion_runs WHERE id=:id
        """),{"id":run_id}).mappings().one_or_none()
        if run is None:
            raise LookupError("Validation expansion run not found")
        results=db.execute(text("""
          SELECT id,task_id,provider,query,source_url,source_domain,source_class,
                 title,mapping_score,matched_dimensions,useful_dimensions,
                 decision,decision_reason,ingested,evidence_id,result_snapshot,created_at
          FROM validation_expansion_results
          WHERE run_id=:id
          ORDER BY created_at,id
        """),{"id":run_id}).mappings().all()

    payload=dict(run)
    payload["id"]=str(payload["id"])
    payload["opportunity_id"]=str(payload["opportunity_id"]) if payload["opportunity_id"] else None
    payload["results"]=[
        {
            **dict(row),
            "id":str(row["id"]),
            "evidence_id":str(row["evidence_id"]) if row["evidence_id"] else None,
            "mapping_score":float(row["mapping_score"] or 0),
        }
        for row in results
    ]
    return payload
