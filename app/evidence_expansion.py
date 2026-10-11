from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from app.aggregator import aggregate_opportunity
from app.classifier import classify
from app.clustering import semantic_tokens, similarity
from app.config import settings
from app.db import engine
from app.evidence_quality import evidence_metrics, normalized_domain
from app.evidence_search_plan import build_evidence_search_plan
from app.providers.evidence_community import search_community
from app.research import generate_research_report
from app.validation import validate_research


MAPPING_ACCEPT_THRESHOLD=0.42
MAPPING_AMBIGUOUS_THRESHOLD=0.25


def _mapping_decision(
    *,
    search_seed: str,
    opportunity_title: str,
    candidate_title: str,
    candidate_text: str,
) -> dict:
    seed=set(semantic_tokens(search_seed))
    candidate=set(semantic_tokens((candidate_title+" "+candidate_text[:5000]).strip()))
    shared=sorted(seed & candidate)
    coverage=(len(shared)/len(seed)) if seed else 0.0
    title_similarity=similarity(opportunity_title,candidate_title)
    normalized_seed=re.sub(r"\s+"," ",search_seed.lower()).strip()
    hay=re.sub(r"\s+"," ",(candidate_title+" "+candidate_text).lower())
    phrase_bonus=0.10 if normalized_seed and normalized_seed in hay else 0.0
    score=min(1.0,coverage*0.65+title_similarity*0.25+phrase_bonus)
    required_shared=1 if len(seed)<=2 else 2

    if len(shared)>=required_shared and score>=MAPPING_ACCEPT_THRESHOLD:
        decision="ACCEPTED"
        reason="semantic seed coverage and title similarity meet controlled mapping threshold"
    elif shared and score>=MAPPING_AMBIGUOUS_THRESHOLD:
        decision="AMBIGUOUS"
        reason="partial semantic overlap requires human or later model review"
    else:
        decision="REJECTED"
        reason="insufficient semantic overlap with target opportunity"

    return {
        "decision":decision,
        "score":round(score,4),
        "shared_tokens":shared,
        "coverage":round(coverage,4),
        "title_similarity":round(title_similarity,4),
        "reason":reason,
    }


def _snapshot(opportunity_id: UUID) -> dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT id,title,status,score,independent_source_count,evidence_count,
                 evidence_quality_score,source_diversity_score,signal_strength_score,
                 evidence_gate_passed,build_readiness,research_validation_score,
                 build_proposal_status,updated_at
          FROM digital_asset_opportunities
          WHERE id=:id
        """),{"id":opportunity_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Opportunity not found")
    return {
        "id":str(row["id"]),
        "title":row["title"],
        "status":row["status"],
        "score":float(row["score"] or 0),
        "independent_source_count":int(row["independent_source_count"] or 0),
        "evidence_count":int(row["evidence_count"] or 0),
        "evidence_quality_score":float(row["evidence_quality_score"] or 0),
        "source_diversity_score":float(row["source_diversity_score"] or 0),
        "signal_strength_score":float(row["signal_strength_score"] or 0),
        "evidence_gate_passed":bool(row["evidence_gate_passed"]),
        "build_readiness":row["build_readiness"],
        "research_validation_score":float(row["research_validation_score"] or 0),
        "build_proposal_status":row["build_proposal_status"],
        "updated_at":row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _create_run(opportunity_id: UUID, plan: dict, requested_by: str) -> UUID:
    initial=_snapshot(opportunity_id)
    with engine.begin() as db:
        return db.execute(text("""
          INSERT INTO evidence_expansion_runs(
            opportunity_id,plan_version,run_status,requested_by,search_seed,initial_snapshot)
          VALUES(:opportunity_id,:plan_version,'RUNNING',:requested_by,:search_seed,
                 CAST(:initial_snapshot AS jsonb))
          RETURNING id
        """),{
            "opportunity_id":opportunity_id,
            "plan_version":plan["version"],
            "requested_by":requested_by[:200],
            "search_seed":plan["search_seed"],
            "initial_snapshot":json.dumps(initial,sort_keys=True),
        }).scalar_one()


def _record_result(
    *,
    run_id: UUID,
    task_id: str,
    provider: str,
    query: str,
    candidate: dict,
    mapping: dict,
    decision: str | None = None,
    reason: str | None = None,
    ingested: bool = False,
    resulting_opportunity_id: str | None = None,
    result_snapshot: dict | None = None,
) -> None:
    final_decision=decision or mapping["decision"]
    final_reason=reason or mapping["reason"]
    with engine.begin() as db:
        db.execute(text("""
          INSERT INTO evidence_expansion_results(
            run_id,task_id,provider,query,source_url,source_domain,source_class,
            title,excerpt,mapping_score,decision,decision_reason,ingested,
            resulting_opportunity_id,result_snapshot)
          VALUES(
            :run_id,:task_id,:provider,:query,:source_url,:source_domain,:source_class,
            :title,:excerpt,:mapping_score,:decision,:decision_reason,:ingested,
            :resulting_opportunity_id,CAST(:result_snapshot AS jsonb))
          ON CONFLICT(run_id,source_url) DO NOTHING
        """),{
            "run_id":run_id,
            "task_id":task_id,
            "provider":provider,
            "query":query[:300],
            "source_url":candidate["url"],
            "source_domain":normalized_domain(candidate["url"]),
            "source_class":candidate["source_class"],
            "title":candidate["title"][:1000],
            "excerpt":(candidate.get("text") or "")[:2000],
            "mapping_score":mapping["score"],
            "decision":final_decision,
            "decision_reason":final_reason[:1000],
            "ingested":ingested,
            "resulting_opportunity_id":resulting_opportunity_id,
            "result_snapshot":json.dumps(result_snapshot or {
                "shared_tokens":mapping["shared_tokens"],
                "coverage":mapping["coverage"],
                "title_similarity":mapping["title_similarity"],
            },sort_keys=True),
        })


def _targeted_ingest(opportunity_id: UUID, candidate: dict, mapping_score: float) -> dict:
    title=candidate["title"]
    content=(candidate.get("text") or "").strip()
    url=candidate["url"]
    if len(content)<20:
        content=(title+" "+url).strip()

    source_type=candidate.get("source_type") or "EVIDENCE_EXPANSION"
    fingerprint=hashlib.sha256((source_type+"|"+url).encode("utf-8")).hexdigest()
    doc_hash=hashlib.sha256(content.encode("utf-8")).hexdigest()
    _,hits,pain=classify(title,content)
    confidence=min(.95,.55+float(mapping_score)*.35)
    source_class,source_quality,signal_strength=evidence_metrics(
        source_type=source_type,
        url=url,
        hits=hits,
        pain=pain,
        confidence=confidence,
    )
    source_domain=normalized_domain(url)

    with engine.begin() as db:
        doc_id=db.execute(text("""
          INSERT INTO documents(url,title,content,content_hash)
          VALUES(:url,:title,:content,:hash)
          ON CONFLICT(url) DO UPDATE SET
            title=excluded.title,
            content=excluded.content,
            content_hash=excluded.content_hash,
            fetched_at=now()
          RETURNING id
        """),{
            "url":url,
            "title":title,
            "content":content[:100000],
            "hash":doc_hash,
        }).scalar_one()

        evidence_id=db.execute(text("""
          INSERT INTO evidence(
            document_id,signal_type,excerpt,source_url,confidence,fingerprint,
            source_domain,source_quality,signal_strength,source_class,last_seen_at)
          VALUES(
            :document_id,:signal_type,:excerpt,:source_url,:confidence,:fingerprint,
            :source_domain,:source_quality,:signal_strength,:source_class,now())
          ON CONFLICT(fingerprint) WHERE fingerprint IS NOT NULL
          DO UPDATE SET
            document_id=excluded.document_id,
            signal_type=excluded.signal_type,
            excerpt=excluded.excerpt,
            source_url=excluded.source_url,
            confidence=excluded.confidence,
            source_domain=excluded.source_domain,
            source_quality=excluded.source_quality,
            signal_strength=excluded.signal_strength,
            source_class=excluded.source_class,
            last_seen_at=now()
          RETURNING id
        """),{
            "document_id":doc_id,
            "signal_type":source_type,
            "excerpt":content[:1200],
            "source_url":url,
            "confidence":confidence,
            "fingerprint":fingerprint,
            "source_domain":source_domain,
            "source_quality":source_quality,
            "signal_strength":signal_strength,
            "source_class":source_class,
        }).scalar_one()

        db.execute(text("""
          INSERT INTO opportunity_evidence(opportunity_id,evidence_id)
          VALUES(:opportunity_id,:evidence_id)
          ON CONFLICT DO NOTHING
        """),{
            "opportunity_id":opportunity_id,
            "evidence_id":evidence_id,
        })

    aggregation=aggregate_opportunity(opportunity_id)
    report_generated=False
    validation_generated=False
    if aggregation["evidence_gate_passed"] and aggregation["status"]=="CANDIDATE":
        try:
            report_generated=bool(generate_research_report(UUID(aggregation["opportunity_id"])))
            validation_generated=bool(validate_research(UUID(aggregation["opportunity_id"])))
        except Exception as exc:
            print(f"evidence expansion research refresh skipped: {type(exc).__name__}",flush=True)

    return {
        "evidence_id":str(evidence_id),
        "source_domain":source_domain,
        "source_class":source_class,
        "source_quality":source_quality,
        "signal_strength":signal_strength,
        "aggregation":aggregation,
        "research_report_generated":report_generated,
        "research_validation_generated":validation_generated,
    }


def _finish_run(
    run_id: UUID,
    *,
    status: str,
    opportunity_id: UUID,
    counts: dict,
    error: str | None = None,
) -> dict:
    final=_snapshot(opportunity_id)
    with engine.begin() as db:
        db.execute(text("""
          UPDATE evidence_expansion_runs
          SET run_status=:status,
              queries_attempted=:queries,
              candidates_found=:candidates,
              accepted_count=:accepted,
              rejected_count=:rejected,
              ambiguous_count=:ambiguous,
              final_snapshot=CAST(:final_snapshot AS jsonb),
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
            "final_snapshot":json.dumps(final,sort_keys=True),
            "error":error[:4000] if error else None,
            "id":run_id,
        })
    return {
        "run_id":str(run_id),
        "status":status,
        "counts":counts,
        "final":final,
    }


def execute_evidence_expansion(
    opportunity_id: str,
    *,
    requested_by: str = "controlled-api",
) -> dict:
    if not settings.evidence_expansion_enabled:
        raise RuntimeError("Evidence expansion execution is disabled")

    target_id=UUID(str(opportunity_id))
    plan=build_evidence_search_plan(target_id)
    if not plan["eligibility"]["recommended_for_expansion"]:
        raise RuntimeError(
            "Opportunity is not eligible for controlled evidence expansion: "
            + ",".join(plan["eligibility"]["reasons"])
        )

    run_id=_create_run(target_id,plan,requested_by)
    counts={"queries":0,"candidates":0,"accepted":0,"rejected":0,"ambiguous":0}
    existing_domains=set(plan["current_evidence"]["source_domains"])
    seen_urls=set()
    max_queries=min(max(int(settings.evidence_expansion_max_queries),1),8)
    per_query=min(max(int(settings.evidence_expansion_results_per_query),1),10)
    max_accepted=min(max(int(settings.evidence_expansion_max_accepted),1),2)

    try:
        for task in plan["tasks"]:
            if counts["queries"]>=max_queries or counts["accepted"]>=max_accepted:
                break
            if task["source_class"]!="community":
                continue

            queries=[plan["search_seed"]]
            queries.extend(task["queries"][:1])
            for query in dict.fromkeys(queries):
                if counts["queries"]>=max_queries or counts["accepted"]>=max_accepted:
                    break
                counts["queries"]+=1
                candidates,provider_errors=search_community(query,per_query)
                if provider_errors:
                    print(
                        "evidence expansion provider warnings: "
                        + ",".join(x["provider"] for x in provider_errors),
                        flush=True,
                    )

                for candidate in candidates:
                    if counts["accepted"]>=max_accepted:
                        break
                    url=candidate["url"]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    counts["candidates"]+=1
                    domain=normalized_domain(url)

                    mapping=_mapping_decision(
                        search_seed=plan["search_seed"],
                        opportunity_title=plan["opportunity"]["title"],
                        candidate_title=candidate["title"],
                        candidate_text=candidate.get("text") or "",
                    )

                    if domain in existing_domains:
                        counts["rejected"]+=1
                        _record_result(
                            run_id=run_id,task_id=task["task_id"],
                            provider=candidate["provider"],query=query,candidate=candidate,
                            mapping=mapping,decision="DUPLICATE",
                            reason="source domain already exists on target Opportunity",
                        )
                        continue

                    if (
                        plan["objective"]["prefer_new_source_class"]
                        and candidate["source_class"] in set(plan["current_evidence"]["source_classes"])
                    ):
                        counts["rejected"]+=1
                        _record_result(
                            run_id=run_id,task_id=task["task_id"],
                            provider=candidate["provider"],query=query,candidate=candidate,
                            mapping=mapping,decision="REJECTED",
                            reason="candidate would not improve Source Diversity",
                        )
                        continue

                    if mapping["decision"]=="AMBIGUOUS":
                        counts["ambiguous"]+=1
                        _record_result(
                            run_id=run_id,task_id=task["task_id"],
                            provider=candidate["provider"],query=query,candidate=candidate,
                            mapping=mapping,
                        )
                        continue
                    if mapping["decision"]!="ACCEPTED":
                        counts["rejected"]+=1
                        _record_result(
                            run_id=run_id,task_id=task["task_id"],
                            provider=candidate["provider"],query=query,candidate=candidate,
                            mapping=mapping,
                        )
                        continue

                    ingest=_targeted_ingest(target_id,candidate,mapping["score"])
                    counts["accepted"]+=1
                    resulting_id=ingest["aggregation"]["opportunity_id"]
                    _record_result(
                        run_id=run_id,task_id=task["task_id"],
                        provider=candidate["provider"],query=query,candidate=candidate,
                        mapping=mapping,ingested=True,
                        resulting_opportunity_id=resulting_id,
                        result_snapshot=ingest,
                    )
                    target_id=UUID(resulting_id)
                    existing_domains.add(domain)

                    if ingest["aggregation"]["evidence_gate_passed"]:
                        break

        status="SUCCEEDED" if counts["accepted"] else "NO_MATCH"
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


def get_evidence_expansion_run(run_id: UUID) -> dict:
    with engine.connect() as db:
        run=db.execute(text("""
          SELECT *
          FROM evidence_expansion_runs
          WHERE id=:id
        """),{"id":run_id}).mappings().one_or_none()
        if run is None:
            raise LookupError("Evidence expansion run not found")
        results=db.execute(text("""
          SELECT id,task_id,provider,query,source_url,source_domain,source_class,
                 title,mapping_score,decision,decision_reason,ingested,
                 resulting_opportunity_id,result_snapshot,created_at
          FROM evidence_expansion_results
          WHERE run_id=:id
          ORDER BY created_at,id
        """),{"id":run_id}).mappings().all()

    payload=dict(run)
    payload["id"]=str(payload["id"])
    payload["opportunity_id"]=str(payload["opportunity_id"])
    payload["results"]=[
        {
            **dict(row),
            "id":str(row["id"]),
            "resulting_opportunity_id":(
                str(row["resulting_opportunity_id"])
                if row["resulting_opportunity_id"] else None
            ),
            "mapping_score":float(row["mapping_score"] or 0),
        }
        for row in results
    ]
    return payload
