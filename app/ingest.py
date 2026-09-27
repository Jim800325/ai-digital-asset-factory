import hashlib
from urllib.parse import urlparse
from sqlalchemy import text
from app.db import engine
from app.classifier import classify
from app.scoring import score_asset, status_for
from app.aggregator import aggregate_opportunity

def ingest_discovery_item(item: dict) -> bool:
    title=item.get("title") or item["url"]
    content=item.get("text") or ""
    if len(content) < 20:
        return False
    kind,hits,pain=classify(title,content)
    if hits < 1:
        return False
    doc_hash=hashlib.sha256(content.encode("utf-8")).hexdigest()
    with engine.begin() as db:
        doc_id=db.execute(text("""
          INSERT INTO documents(url,title,content,content_hash)
          VALUES(:u,:t,:c,:h)
          ON CONFLICT(url) DO UPDATE SET title=excluded.title,content=excluded.content,
            content_hash=excluded.content_hash,fetched_at=now()
          RETURNING id
        """),{"u":item["url"],"t":title,"c":content,"h":doc_hash}).scalar_one()
        ev_id=db.execute(text("""
          INSERT INTO evidence(document_id,signal_type,excerpt,source_url,confidence,fingerprint,source_domain)
          VALUES(:d,:s,:e,:u,:cf,:fp,:domain)
          ON CONFLICT(fingerprint) WHERE fingerprint IS NOT NULL
          DO UPDATE SET excerpt=excluded.excerpt,confidence=excluded.confidence,discovered_at=now()
          RETURNING id
        """),{
          "d":doc_id,"s":item["source_type"],"e":content[:1200],"u":item["url"],
          "cf":min(.95,.45+hits*.08+pain*.03),"fp":item["fingerprint"],
          "domain":urlparse(item["url"]).netloc.lower()
        }).scalar_one()

        demand=min(100,35+pain*10+hits*4)
        repeat=min(100,55+hits*6)
        automation=min(100,50+hits*7)
        ownership=85 if kind in ("DATASET_API","MICRO_SAAS_TOOL") else 72
        margin=90 if kind in ("DATASET_API","TEMPLATE_WORKFLOW","CONTENT_IP") else 75
        evidence_score=min(100,40+hits*8+pain*5)
        score=score_asset(demand=demand,repeatability=repeat,automation=automation,
                          ownership=ownership,margin=margin,evidence=evidence_score)
        fp=hashlib.sha256((kind+"|"+title.lower()[:180]).encode()).hexdigest()
        opp_id=db.execute(text("""
          INSERT INTO digital_asset_opportunities(
            fingerprint,title,asset_type,problem,target_customer,monetization_model,source_url,
            demand_score,repeatability_score,automation_score,ownership_score,marginal_cost_score,
            evidence_score,score,status)
          VALUES(:fp,:title,:kind,:problem,:customer,:money,:url,:d,:r,:a,:o,:m,:e,:score,:status)
          ON CONFLICT(fingerprint) DO UPDATE SET score=excluded.score,updated_at=now()
          RETURNING id
        """),{
          "fp":fp,"title":title,"kind":kind,
          "problem":"Detected digital-asset demand signal; independent evidence required.",
          "customer":"Pending cross-source validation",
          "money":"subscription / one-time sale / API / licensing","url":item["url"],
          "d":demand,"r":repeat,"a":automation,"o":ownership,"m":margin,"e":evidence_score,
          "score":score,"status":status_for(score,1)
        }).scalar_one()
        db.execute(text("""
          INSERT INTO opportunity_evidence(opportunity_id,evidence_id)
          VALUES(:o,:e) ON CONFLICT DO NOTHING
        """),{"o":opp_id,"e":ev_id})
    aggregate_opportunity(opp_id)
    return True
