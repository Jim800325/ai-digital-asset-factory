from sqlalchemy import text

from app.db import engine
from app.clustering import cluster_key, similarity
from app.evidence_quality import diversity_score, evidence_quality
from app.scoring import status_for

MATCH_THRESHOLD = 0.45

def _evidence_rows(db, opportunity_id):
    return db.execute(text("""
      SELECT e.id,e.source_domain,e.source_url,e.source_class,
             e.source_quality,e.signal_strength
      FROM opportunity_evidence oe
      JOIN evidence e ON e.id=oe.evidence_id
      WHERE oe.opportunity_id=:id
    """),{"id":opportunity_id}).mappings().all()

def aggregate_opportunity(opportunity_id) -> dict:
    with engine.begin() as db:
        current=db.execute(text("""
          SELECT id,title,asset_type,score FROM digital_asset_opportunities WHERE id=:id
        """),{"id":opportunity_id}).mappings().one()
        target=cluster_key(current["asset_type"],current["title"])

        candidates=db.execute(text("""
          SELECT id,title,canonical_title FROM digital_asset_opportunities
          WHERE asset_type=:asset_type AND id<>:id
          ORDER BY updated_at DESC LIMIT 300
        """),{"asset_type":current["asset_type"],"id":opportunity_id}).mappings().all()

        best=None
        best_score=0.0
        for row in candidates:
            s=similarity(current["title"],row["canonical_title"] or row["title"])
            if s>best_score:
                best,best_score=row,s

        if best is not None and best_score>=MATCH_THRESHOLD:
            survivor=best["id"]
            db.execute(text("""
              INSERT INTO opportunity_evidence(opportunity_id,evidence_id)
              SELECT :survivor,evidence_id FROM opportunity_evidence WHERE opportunity_id=:loser
              ON CONFLICT DO NOTHING
            """),{"survivor":survivor,"loser":opportunity_id})
            db.execute(text("""
              INSERT INTO opportunity_aliases(opportunity_id,alias,normalized_alias,source_url)
              SELECT :survivor,title,lower(title),source_url
              FROM digital_asset_opportunities WHERE id=:loser
              ON CONFLICT DO NOTHING
            """),{"survivor":survivor,"loser":opportunity_id})
            db.execute(text("DELETE FROM digital_asset_opportunities WHERE id=:loser"),
                       {"loser":opportunity_id})
            opportunity_id=survivor
            confidence=best_score
        else:
            db.execute(text("""
              UPDATE digital_asset_opportunities
              SET canonical_key=:key,canonical_title=:title,cluster_confidence=:confidence
              WHERE id=:id
            """),{"key":target.key,"title":target.canonical_title,
                   "confidence":target.confidence,"id":opportunity_id})
            confidence=target.confidence

        evidence_rows=_evidence_rows(db,opportunity_id)
        domains={
            (row["source_domain"] or row["source_url"])
            for row in evidence_rows if row["source_domain"] or row["source_url"]
        }
        classes=[row["source_class"] for row in evidence_rows]
        qualities=[float(row["source_quality"]) for row in evidence_rows]
        strengths=[float(row["signal_strength"]) for row in evidence_rows]
        sources=len(domains)
        evidence_count=len({row["id"] for row in evidence_rows})
        diversity=diversity_score(classes)
        signal=round(sum(strengths)/len(strengths),2) if strengths else 0.0
        quality=evidence_quality(qualities,strengths,diversity)

        row=db.execute(text("SELECT score FROM digital_asset_opportunities WHERE id=:id"),
                       {"id":opportunity_id}).mappings().one()
        score=float(row["score"])
        status=status_for(
            score,sources,evidence_quality=quality,
            source_diversity=diversity,signal_strength=signal
        )
        gate=status=="CANDIDATE"
        db.execute(text("""
          UPDATE digital_asset_opportunities
          SET independent_source_count=:sources,evidence_count=:evidence,
              cluster_confidence=:confidence,evidence_quality_score=:quality,
              source_diversity_score=:diversity,signal_strength_score=:signal,
              evidence_gate_passed=:gate,status=:status,updated_at=now()
          WHERE id=:id
        """),{"sources":sources,"evidence":evidence_count,"confidence":confidence,
               "quality":quality,"diversity":diversity,"signal":signal,
               "gate":gate,"status":status,"id":opportunity_id})
        return {
            "opportunity_id":str(opportunity_id),
            "independent_sources":sources,
            "evidence_count":evidence_count,
            "evidence_quality":quality,
            "source_diversity":diversity,
            "signal_strength":signal,
            "evidence_gate_passed":gate,
            "status":status,
            "cluster_confidence":round(float(confidence),4),
        }
