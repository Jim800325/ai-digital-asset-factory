from sqlalchemy import text
from app.db import engine
from app.clustering import cluster_key, similarity
from app.scoring import status_for

MATCH_THRESHOLD = 0.45

def _domain_sql():
    return """
      SELECT COUNT(DISTINCT COALESCE(NULLIF(e.source_domain,''), e.source_url)) AS sources,
             COUNT(DISTINCT e.id) AS evidence
      FROM opportunity_evidence oe
      JOIN evidence e ON e.id=oe.evidence_id
      WHERE oe.opportunity_id=:id
    """

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

        counts=db.execute(text(_domain_sql()),{"id":opportunity_id}).mappings().one()
        row=db.execute(text("SELECT score FROM digital_asset_opportunities WHERE id=:id"),
                       {"id":opportunity_id}).mappings().one()
        status=status_for(float(row["score"]),int(counts["sources"]))
        db.execute(text("""
          UPDATE digital_asset_opportunities
          SET independent_source_count=:sources,evidence_count=:evidence,
              cluster_confidence=:confidence,status=:status,updated_at=now()
          WHERE id=:id
        """),{"sources":counts["sources"],"evidence":counts["evidence"],
               "confidence":confidence,"status":status,"id":opportunity_id})
        return {"opportunity_id":str(opportunity_id),"independent_sources":counts["sources"],
                "evidence_count":counts["evidence"],"status":status,
                "cluster_confidence":round(float(confidence),4)}
