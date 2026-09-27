# AI Digital Asset Factory

Autonomous discovery and intelligence pipeline focused on **repeatable digital assets**, not client-service opportunities.

## v0.1

`discovery -> crawl -> evidence -> digital asset opportunity -> score -> PostgreSQL`

Asset classes:
- DATASET_API
- INTELLIGENCE_REPORT
- MICRO_SAAS_TOOL
- TEMPLATE_WORKFLOW
- CONTENT_IP

Safety boundary: read/analyze/store/report only. No purchasing, account registration, outreach, publishing, or product deployment.

## Start

```bash
cp .env.example .env
docker compose up --build
```

Open http://localhost:8000/docs and run `POST /v1/runs`.
