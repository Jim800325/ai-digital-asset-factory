import hashlib

from sqlalchemy import text

from app.aggregator import aggregate_opportunity
from app.classifier import classify
from app.db import engine
from app.evidence_quality import evidence_metrics, normalized_domain
from app.research import generate_research_report, mark_research_report_stale
from app.scoring import score_asset, status_for
from app.validation import mark_validation_not_ready, validate_research

def ingest_discovery_item(item: dict):
    title=item.get("title") or item["url"]
    content=item.get("text") or ""
    if len(content) < 20:
        return None

    kind,hits,pain=classify(title,content)
    if hits < 1:
        return None

    source_type=item.get("source_type") or "UNKNOWN"
    evidence_fingerprint=item.get("fingerprint") or hashlib.sha256(
        (source_type+"|"+item["url"]).encode("utf-8")
    ).hexdigest()
    doc_hash=hashlib.sha256(content.encode("utf-8")).hexdigest()
    confidence=min(.95,.45+hits*.08+pain*.03)
    source_class,source_quality,signal_strength=evidence_metrics(
        source_type=source_type,
        url=item["url"],
        hits=hits,
        pain=pain,
        confidence=confidence,
    )
    source_domain=normalized_domain(item["url"])

    demand=min(100,35+pain*10+hits*4)
    repeat=min(100,55+hits*6)
    automation=min(100,50+hits*7)
    ownership=85 if kind in ("DATASET_API","MICRO_SAAS_TOOL") else 72
    margin=90 if kind in ("DATASET_API","TEMPLATE_WORKFLOW","CONTENT_IP") else 75
    evidence_score=min(100,40+hits*8+pain*5)
    score=score_asset(
        demand=demand,
        repeatability=repeat,
        automation=automation,
        ownership=ownership,
        margin=margin,
        evidence=evidence_score,
    )
    opportunity_fingerprint=hashlib.sha256(
        (kind+"|"+title.lower()[:180]).encode("utf-8")
    ).hexdigest()

    with engine.begin() as db:
        doc_id=db.execute(text("""
          INSERT INTO documents(url,title,content,content_hash)
          VALUES(:u,:t,:c,:h)
          ON CONFLICT(url) DO UPDATE SET
            title=excluded.title,
            content=excluded.content,
            content_hash=excluded.content_hash,
            fetched_at=now()
          RETURNING id
        """),{
            "u":item["url"],
            "t":title,
            "c":content,
            "h":doc_hash,
        }).scalar_one()

        existing_evidence=db.execute(text("""
          SELECT id FROM evidence WHERE fingerprint=:fp
        """),{"fp":evidence_fingerprint}).scalar()
        evidence_created=existing_evidence is None

        ev_id=db.execute(text("""
          INSERT INTO evidence(
            document_id,signal_type,excerpt,source_url,confidence,fingerprint,source_domain,
            source_quality,signal_strength,source_class,last_seen_at)
          VALUES(:d,:s,:e,:u,:cf,:fp,:domain,:sq,:ss,:sc,now())
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
            "d":doc_id,
            "s":source_type,
            "e":content[:1200],
            "u":item["url"],
            "cf":confidence,
            "fp":evidence_fingerprint,
            "domain":source_domain,
            "sq":source_quality,
            "ss":signal_strength,
            "sc":source_class,
        }).scalar_one()

        mapped=db.execute(text("""
          SELECT opportunity_id
          FROM opportunity_fingerprints
          WHERE fingerprint=:fp
        """),{"fp":opportunity_fingerprint}).scalar()

        opportunity_created=False
        if mapped is not None:
            opp_id=mapped
            db.execute(text("""
              UPDATE opportunity_fingerprints
              SET last_seen_at=now()
              WHERE fingerprint=:fp
            """),{"fp":opportunity_fingerprint})
            db.execute(text("""
              UPDATE digital_asset_opportunities
              SET demand_score=GREATEST(demand_score,:d),
                  repeatability_score=GREATEST(repeatability_score,:r),
                  automation_score=GREATEST(automation_score,:a),
                  ownership_score=GREATEST(ownership_score,:o),
                  marginal_cost_score=GREATEST(marginal_cost_score,:m),
                  evidence_score=GREATEST(evidence_score,:e),
                  score=GREATEST(score,:score),
                  updated_at=now()
              WHERE id=:id
            """),{
                "d":demand,
                "r":repeat,
                "a":automation,
                "o":ownership,
                "m":margin,
                "e":evidence_score,
                "score":score,
                "id":opp_id,
            })
        else:
            existing_opportunity=db.execute(text("""
              SELECT id
              FROM digital_asset_opportunities
              WHERE fingerprint=:fp
            """),{"fp":opportunity_fingerprint}).scalar()
            opportunity_created=existing_opportunity is None

            opp_id=db.execute(text("""
              INSERT INTO digital_asset_opportunities(
                fingerprint,title,asset_type,problem,target_customer,monetization_model,source_url,
                demand_score,repeatability_score,automation_score,ownership_score,marginal_cost_score,
                evidence_score,score,status)
              VALUES(
                :fp,:title,:kind,:problem,:customer,:money,:url,
                :d,:r,:a,:o,:m,:e,:score,:status)
              ON CONFLICT(fingerprint) DO UPDATE SET
                demand_score=GREATEST(digital_asset_opportunities.demand_score,excluded.demand_score),
                repeatability_score=GREATEST(digital_asset_opportunities.repeatability_score,excluded.repeatability_score),
                automation_score=GREATEST(digital_asset_opportunities.automation_score,excluded.automation_score),
                ownership_score=GREATEST(digital_asset_opportunities.ownership_score,excluded.ownership_score),
                marginal_cost_score=GREATEST(digital_asset_opportunities.marginal_cost_score,excluded.marginal_cost_score),
                evidence_score=GREATEST(digital_asset_opportunities.evidence_score,excluded.evidence_score),
                score=GREATEST(digital_asset_opportunities.score,excluded.score),
                updated_at=now()
              RETURNING id
            """),{
                "fp":opportunity_fingerprint,
                "title":title,
                "kind":kind,
                "problem":"Detected digital-asset demand signal; independent evidence required.",
                "customer":"Pending cross-source validation",
                "money":"subscription / one-time sale / API / licensing",
                "url":item["url"],
                "d":demand,
                "r":repeat,
                "a":automation,
                "o":ownership,
                "m":margin,
                "e":evidence_score,
                "score":score,
                "status":status_for(score,1),
            }).scalar_one()

            db.execute(text("""
              INSERT INTO opportunity_fingerprints(fingerprint,opportunity_id)
              VALUES(:fp,:id)
              ON CONFLICT(fingerprint) DO UPDATE SET
                opportunity_id=excluded.opportunity_id,
                last_seen_at=now()
            """),{
                "fp":opportunity_fingerprint,
                "id":opp_id,
            })

        db.execute(text("""
          INSERT INTO opportunity_evidence(opportunity_id,evidence_id)
          VALUES(:o,:e)
          ON CONFLICT DO NOTHING
        """),{
            "o":opp_id,
            "e":ev_id,
        })

    aggregation=aggregate_opportunity(opp_id)
    qualified=(
        aggregation["status"]=="CANDIDATE"
        and aggregation["evidence_gate_passed"]
    )
    if qualified:
        try:
            generate_research_report(aggregation["opportunity_id"])
            validate_research(aggregation["opportunity_id"])
        except Exception as exc:
            print(f"research validation skipped: {exc}", flush=True)
    else:
        try:
            mark_research_report_stale(aggregation["opportunity_id"])
            mark_validation_not_ready(aggregation["opportunity_id"])
        except Exception as exc:
            print(f"research state reconciliation skipped: {exc}", flush=True)

    return {
        "accepted":True,
        "evidence_created":evidence_created,
        "opportunity_created":bool(opportunity_created and not aggregation["merged"]),
        "opportunity_id":aggregation["opportunity_id"],
        "status":aggregation["status"],
        "evidence_gate_passed":aggregation["evidence_gate_passed"],
        "merged":aggregation["merged"],
    }
