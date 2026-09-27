from sqlalchemy import text

from app.db import engine
from app.clustering import cluster_key, similarity
from app.evidence_quality import diversity_score, evidence_quality, normalized_domain
from app.scoring import evidence_gate_for, status_for

MATCH_THRESHOLD = 0.45

def _evidence_rows(db, opportunity_id):
    return db.execute(text("""
      SELECT e.id,e.source_domain,e.source_url,e.source_class,
             e.source_quality,e.signal_strength
      FROM opportunity_evidence oe
      JOIN evidence e ON e.id=oe.evidence_id
      WHERE oe.opportunity_id=:id
    """),{"id":opportunity_id}).mappings().all()

def _merge_into_survivor(db, survivor, loser):
    db.execute(text("""
      INSERT INTO opportunity_evidence(opportunity_id,evidence_id)
      SELECT :survivor,evidence_id
      FROM opportunity_evidence
      WHERE opportunity_id=:loser
      ON CONFLICT DO NOTHING
    """),{"survivor":survivor,"loser":loser})

    db.execute(text("""
      INSERT INTO opportunity_aliases(opportunity_id,alias,normalized_alias,source_url)
      SELECT :survivor,title,lower(title),source_url
      FROM digital_asset_opportunities
      WHERE id=:loser
      ON CONFLICT DO NOTHING
    """),{"survivor":survivor,"loser":loser})

    db.execute(text("""
      INSERT INTO opportunity_aliases(opportunity_id,alias,normalized_alias,source_url)
      SELECT :survivor,alias,normalized_alias,source_url
      FROM opportunity_aliases
      WHERE opportunity_id=:loser
      ON CONFLICT DO NOTHING
    """),{"survivor":survivor,"loser":loser})

    db.execute(text("""
      UPDATE opportunity_fingerprints
      SET opportunity_id=:survivor,last_seen_at=now()
      WHERE opportunity_id=:loser
    """),{"survivor":survivor,"loser":loser})

    db.execute(text("""
      UPDATE digital_asset_opportunities AS s
      SET demand_score=GREATEST(s.demand_score,l.demand_score),
          repeatability_score=GREATEST(s.repeatability_score,l.repeatability_score),
          automation_score=GREATEST(s.automation_score,l.automation_score),
          ownership_score=GREATEST(s.ownership_score,l.ownership_score),
          marginal_cost_score=GREATEST(s.marginal_cost_score,l.marginal_cost_score),
          evidence_score=GREATEST(s.evidence_score,l.evidence_score),
          score=GREATEST(s.score,l.score),
          updated_at=now()
      FROM digital_asset_opportunities AS l
      WHERE s.id=:survivor AND l.id=:loser
    """),{"survivor":survivor,"loser":loser})

    db.execute(
        text("DELETE FROM digital_asset_opportunities WHERE id=:loser"),
        {"loser":loser},
    )

def _aggregate_evidence_metrics(evidence_rows):
    domains={}
    evidence_ids=set()
    for row in evidence_rows:
        evidence_ids.add(row["id"])
        domain=normalized_domain(row["source_url"]) or (row["source_domain"] or "").lower()
        if not domain:
            continue
        bucket=domains.setdefault(domain,{"quality":0.0,"strength":0.0,"classes":set()})
        bucket["quality"]=max(bucket["quality"],float(row["source_quality"] or 0))
        bucket["strength"]=max(bucket["strength"],float(row["signal_strength"] or 0))
        if row["source_class"]:
            bucket["classes"].add(row["source_class"])

    qualities=[bucket["quality"] for bucket in domains.values()]
    strengths=[bucket["strength"] for bucket in domains.values()]
    classes=[source_class for bucket in domains.values() for source_class in bucket["classes"]]

    diversity=diversity_score(classes)
    signal=round(sum(strengths)/len(strengths),2) if strengths else 0.0
    quality=evidence_quality(qualities,strengths,diversity)
    return {
        "sources":len(domains),
        "evidence_count":len(evidence_ids),
        "diversity":diversity,
        "signal":signal,
        "quality":quality,
    }

def aggregate_opportunity(opportunity_id) -> dict:
    with engine.begin() as db:
        current=db.execute(text("""
          SELECT id,title,asset_type,score
          FROM digital_asset_opportunities
          WHERE id=:id
        """),{"id":opportunity_id}).mappings().one()
        target=cluster_key(current["asset_type"],current["title"])

        candidates=db.execute(text("""
          SELECT id,title,canonical_title
          FROM digital_asset_opportunities
          WHERE asset_type=:asset_type AND id<>:id
          ORDER BY updated_at DESC
          LIMIT 300
        """),{"asset_type":current["asset_type"],"id":opportunity_id}).mappings().all()

        best=None
        best_score=0.0
        for row in candidates:
            candidate_title=row["canonical_title"] or row["title"]
            match_score=similarity(current["title"],candidate_title)
            if match_score>best_score:
                best,best_score=row,match_score

        if best is not None and best_score>=MATCH_THRESHOLD:
            survivor=best["id"]
            _merge_into_survivor(db,survivor,opportunity_id)
            opportunity_id=survivor
            survivor_row=db.execute(text("""
              SELECT title,asset_type,canonical_key,canonical_title,cluster_confidence
              FROM digital_asset_opportunities
              WHERE id=:id
            """),{"id":opportunity_id}).mappings().one()
            survivor_target=cluster_key(survivor_row["asset_type"],survivor_row["title"])
            db.execute(text("""
              UPDATE digital_asset_opportunities
              SET canonical_key=COALESCE(canonical_key,:key),
                  canonical_title=COALESCE(canonical_title,:title),
                  cluster_confidence=GREATEST(cluster_confidence,:confidence),
                  updated_at=now()
              WHERE id=:id
            """),{
                "key":survivor_target.key,
                "title":survivor_target.canonical_title,
                "confidence":best_score,
                "id":opportunity_id,
            })
            confidence=max(float(survivor_row["cluster_confidence"] or 0),best_score)
        else:
            db.execute(text("""
              UPDATE digital_asset_opportunities
              SET canonical_key=:key,canonical_title=:title,cluster_confidence=:confidence
              WHERE id=:id
            """),{
                "key":target.key,
                "title":target.canonical_title,
                "confidence":target.confidence,
                "id":opportunity_id,
            })
            confidence=target.confidence

        metrics=_aggregate_evidence_metrics(_evidence_rows(db,opportunity_id))
        row=db.execute(
            text("SELECT score FROM digital_asset_opportunities WHERE id=:id"),
            {"id":opportunity_id},
        ).mappings().one()
        score=float(row["score"])

        gate=evidence_gate_for(
            metrics["sources"],
            evidence_quality=metrics["quality"],
            source_diversity=metrics["diversity"],
            signal_strength=metrics["signal"],
        )
        status=status_for(
            score,
            metrics["sources"],
            evidence_quality=metrics["quality"],
            source_diversity=metrics["diversity"],
            signal_strength=metrics["signal"],
        )

        db.execute(text("""
          UPDATE digital_asset_opportunities
          SET independent_source_count=:sources,
              evidence_count=:evidence,
              cluster_confidence=:confidence,
              evidence_quality_score=:quality,
              source_diversity_score=:diversity,
              signal_strength_score=:signal,
              evidence_gate_passed=:gate,
              status=:status,
              updated_at=now()
          WHERE id=:id
        """),{
            "sources":metrics["sources"],
            "evidence":metrics["evidence_count"],
            "confidence":confidence,
            "quality":metrics["quality"],
            "diversity":metrics["diversity"],
            "signal":metrics["signal"],
            "gate":gate,
            "status":status,
            "id":opportunity_id,
        })

        return {
            "opportunity_id":str(opportunity_id),
            "independent_sources":metrics["sources"],
            "evidence_count":metrics["evidence_count"],
            "evidence_quality":metrics["quality"],
            "source_diversity":metrics["diversity"],
            "signal_strength":metrics["signal"],
            "evidence_gate_passed":gate,
            "status":status,
            "cluster_confidence":round(float(confidence),4),
        }
