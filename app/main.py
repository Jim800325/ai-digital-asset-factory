from fastapi import FastAPI
from redis import Redis
from rq import Queue
from sqlalchemy import text
from app.config import settings
from app.db import engine
from app.workers.pipeline import run_pipeline

app = FastAPI(title="AI Digital Asset Factory", version="0.1.0")

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("select 1"))
    return {"status": "ok", "mode": "OBSERVE"}

@app.post("/v1/runs", status_code=202)
def create_run():
    q = Queue("asset-factory", connection=Redis.from_url(settings.redis_url))
    job = q.enqueue(run_pipeline, job_timeout=900)
    return {"job_id": job.id, "status": "queued"}

@app.get("/v1/runs")
def runs(limit: int = 30):
    sql = text("""
      SELECT id,status,pages_discovered,pages_crawled,evidence_created,
             opportunities_created,error,started_at,finished_at
      FROM pipeline_runs ORDER BY started_at DESC LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql, {"limit": min(limit, 200)})]

@app.get("/v1/opportunities")
def opportunities(limit: int = 50):
    sql = text("""
      SELECT id,title,asset_type,score,demand_score,repeatability_score,
             automation_score,ownership_score,marginal_cost_score,evidence_score,
             repeatable_sale,update_automation,status,independent_source_count,evidence_count,cluster_confidence,canonical_title,monetization_model,source_url,created_at
      FROM digital_asset_opportunities
      ORDER BY score DESC, created_at DESC LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql, {"limit": min(limit, 200)})]
