# Controlled Production Release Executor v0.1 — Step 4 Vercel Controlled PREPARE

Status: **IMPLEMENTATION STEP 4 — SACRIFICIAL PREVIEW TARGET ONLY**

This step introduces the first real provider mutation that the executor can perform. It is limited to creating a staged Vercel deployment in an explicitly allowlisted sacrificial project. It does not promote traffic.

## Verified Vercel primitive

The implementation mirrors Vercel's staged production deployment behavior:

- Create Deployment uses `target=production` so the deployment receives Production configuration.
- Create Deployment sets `autoAssignCustomDomains=false`, which is the REST equivalent used by the Vercel CLI `--prod --skip-domain` flow.
- No alias API, promote API, rollback API, rolling-release API, or domain mutation is called.

## Hard target policy

The Vercel adapter requires:

- exact `target_provider=VERCEL`
- exact `target_environment=production`
- non-empty project and team IDs
- project ID present in `PRODUCTION_EXECUTION_ALLOWED_PROJECT_IDS`
- team ID present in `PRODUCTION_EXECUTION_ALLOWED_TEAM_IDS`
- Preview runtime when `PRODUCTION_EXECUTION_PREVIEW_ONLY=true`
- dedicated `VERCEL_CONTROLLED_EXECUTOR_TOKEN`

The real project is hard denylisted in code and is also the default configured denylist:

`prj_orLCRCIm7aVfImH8ihB3gponFOEl` (`ai-digital-asset-factory`)

Even if that ID is accidentally added to the allowlist, the adapter refuses it.

## Exactly-once PREPARE write budget

Migration `026_vercel_controlled_prepare.sql` adds:

- `PREPARE_UNKNOWN` execution state
- `prepare_request_sha256`
- deterministic `prepare_provider_deployment_id`
- `prepare_provider_state`
- `prepare_outcome`
- `prepare_write_count` constrained to 0 or 1
- provider result/error hashes
- attempted/reconciled timestamps

Before the provider POST, the executor commits the exact request SHA, deterministic deployment ID and consumes the single write budget.

Once `prepare_write_count=1`, no automatic or repeated PREPARE POST is permitted.

## Deterministic provider deployment ID

The Vercel deployment ID is derived from the immutable execution SHA-256 and sent in the Create Deployment request.

This creates a stable reconciliation key even if the HTTP client times out after Vercel accepts the request.

## Ambiguous write policy

The following become `PREPARE_UNKNOWN` / `AMBIGUOUS`:

- HTTP timeout
- transport failure after the write is attempted
- HTTP 5xx response

The executor persists `RECONCILIATION_REQUIRED` and never replays the POST.

## Reconciliation

Reconciliation is read-only:

`GET /v13/deployments/{deterministicDeploymentId}`

Results:

- 404 -> remain pending/unknown; no replay
- BUILDING / INITIALIZING / QUEUED -> reconciliation pending
- READY -> persist exact candidate and reach `READY_FOR_PROMOTION`
- ERROR / CANCELED -> `PREPARE_FAILED`

Every reconciliation keeps `prepare_write_count=1`.

## Provider response safety

A candidate is rejected if:

- returned deployment ID differs from the deterministic ID
- project/team differs from the frozen target
- Vercel reports any assigned alias/domain
- deployment URL is missing

## API surface added

- `POST /v1/deployment-plans/{plan_id}/execution`
- `POST /v1/production-release-executions/{execution_id}/prepare`
- `POST /v1/production-release-executions/{execution_id}/reconcile`

All write-sensitive operations require the independent `X-Production-Execution-Key`.

## Still forbidden

- real `ai-digital-asset-factory` project PREPARE
- Production promote
- alias/domain assignment
- rollback
- rolling release
- automatic second provider write
- automatic promotion

## Live acceptance prerequisite

No sacrificial Vercel project currently exists in the connected team. The adapter therefore remains fail-closed until a dedicated project ID is created and explicitly placed in the project allowlist together with the exact team ID.
