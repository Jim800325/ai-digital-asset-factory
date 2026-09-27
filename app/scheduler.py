import os
import time
from datetime import datetime, timezone
from redis import Redis
from rq import Queue
from app.config import settings
from app.workers.pipeline import run_pipeline

INTERVAL = max(3600, int(os.getenv("SCHEDULE_INTERVAL_SECONDS", "86400")))
START_DELAY = max(10, int(os.getenv("SCHEDULE_START_DELAY_SECONDS", "60")))

def main():
    print(f"scheduler starting; first run in {START_DELAY}s, interval={INTERVAL}s", flush=True)
    time.sleep(START_DELAY)
    redis = Redis.from_url(settings.redis_url)
    queue = Queue("asset-factory", connection=redis)
    while True:
        job = queue.enqueue(run_pipeline, job_timeout=900)
        print(f"{datetime.now(timezone.utc).isoformat()} queued run {job.id}", flush=True)
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
