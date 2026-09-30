# Controlled Production Release Executor v0.1 — Step 4A Live Preview PREPARE Acceptance

Status: **IMPLEMENTED / LIVE RUN BLOCKED BY VERCEL PROJECT + BUILD RATE LIMIT**

Step 4A validates the first real Vercel provider mutation without changing
Production traffic. It targets exactly one dedicated sacrificial project and
stops after the third human PROMOTE authorization has been persisted. Provider
promotion remains disabled.

## Current external blockers

At implementation time the connected Vercel team contains only:

- `ai-digital-asset-factory`
- `tolerance-license-server`
- `mark-six`

No dedicated sacrificial project exists.

The current Step 4 HEAD is also Vercel build-rate-limited, so a fresh Preview
deployment cannot yet be created.

The acceptance remains fail-closed until both conditions are cleared.

## Required Preview configuration

The acceptance Preview must configure:

```text
PREVIEW_DATABASE_URL=<isolated Preview database>
PREVIEW_ACCEPTANCE_KEY=<independent random key>
HUMAN_PRODUCTION_EXECUTION_KEY=<independent random key>

CONTROLLED_PRODUCTION_EXECUTOR_ENABLED=true
PRODUCTION_EXECUTION_ADAPTER=VERCEL_CONTROLLED_EXECUTOR
PRODUCTION_EXECUTION_PREVIEW_ONLY=true

PRODUCTION_EXECUTION_ALLOWED_PROJECT_IDS=<exact sacrificial project id>
PRODUCTION_EXECUTION_ALLOWED_TEAM_IDS=<exact team id>
PRODUCTION_EXECUTION_DENIED_PROJECT_IDS=prj_orLCRCIm7aVfImH8ihB3gponFOEl

VERCEL_CONTROLLED_EXECUTOR_TOKEN=<dedicated token>

PRODUCTION_PROMOTION_ENABLED=false
PRODUCTION_ROLLBACK_ENABLED=false
```

Exactly one project and exactly one team must be in the allowlists.

## Acceptance fixture source

Step 4A does **not** invoke the Live model again.

It selects the latest committed Live Acceptance audit that is:

- `PASSED`
- registry `VERIFIED`
- `live_model_verified=true`
- `tests_passed=true`
- `budget_status=WITHIN_BUDGET`
- `external_side_effects=DENY`

It then finds the artifact bytes previously persisted in the isolated Preview
database by the Preview Acceptance Bridge and clones those exact bytes into a
fresh acceptance-only sandbox request.

Every cloned artifact is rehashed before use.

## Live flow

### 1. Read-only readiness

`GET /internal/vercel-prepare-acceptance/readiness`

This performs no provider mutation.

Required READY conditions include:

- Vercel Preview runtime
- isolated `PREVIEW_DATABASE_URL`
- migration 027 current
- exactly one sacrificial project/team
- real project in denylist
- dedicated executor token present
- independent Preview + Production Execution keys present
- verified Live Acceptance audit available
- matching persisted source artifact bytes available
- promotion/rollback flags false

### 2. Start acceptance

`POST /internal/vercel-prepare-acceptance/start`

Headers:

- `X-Preview-Acceptance-Key`
- `X-Production-Execution-Key`

The endpoint accepts **no target project/team parameters**. The target is read
only from server configuration.

Before the first write it performs:

1. real-project denylist probe
2. read-only sacrificial project lookup
3. verified artifact-byte clone
4. Release Gate APPROVE
5. Deployment Plan creation
6. Deployment Authorization AUTHORIZE
7. immutable execution snapshot
8. Execution Integrity Gate

Then it performs the one allowed provider mutation:

```text
POST /v13/deployments
target=production
autoAssignCustomDomains=false
deterministic deploymentId
```

The database write budget is consumed before this POST.

### 3. Ambiguous reconciliation

If PREPARE returns timeout / transport ambiguity / 5xx:

```text
PREPARE_UNKNOWN
prepare_write_count=1
automatic POST replay=false
```

Use:

`POST /internal/vercel-prepare-acceptance/{run_id}/reconcile`

Reconciliation is GET-only.

### 4. READY boundary

Successful PREPARE must reach:

```text
READY_FOR_PROMOTION
prepare_write_count=1
candidate deployment persisted
production_vercel_deployment_id=null
previous_production_deployment_id=null
Production traffic unchanged
```

No alias/domain may be assigned.

### 5. Third human gate

`POST /internal/vercel-prepare-acceptance/{run_id}/authorize`

This persists the immutable Step 3 `PROMOTE` decision against the exact
candidate. It does **not** call Vercel promote.

Required result:

```text
PROMOTE_AUTHORIZED
provider promotion performed=false
Production traffic changed=false
rollback performed=false
```

### 6. Cleanup

`POST /internal/vercel-prepare-acceptance/{run_id}/cleanup`

Cleanup archives the acceptance-only release fixture and retains all audit
evidence. It deliberately does not delete the sacrificial Vercel deployment,
because provider deletion is outside the Step 4A mutation contract.

## Persistent acceptance audit

Migration `027_vercel_prepare_live_acceptance.sql` adds
`vercel_prepare_acceptance_runs`.

It binds:

- control Preview commit
- source Live Acceptance audit
- source/acceptance sandbox request
- Release Candidate / Review Package
- Deployment Plan
- Production execution snapshot
- sacrificial target
- real project denylist proof
- exact Vercel candidate
- PREPARE write count/outcome
- human PROMOTE decision
- explicit no-promotion / no-rollback / no-traffic-change evidence

## Exit criteria

Step 4A passes only when a real sacrificial Vercel target proves:

```text
denylist probe                  PASS
sacrificial target lookup       PASS
exact artifact clone            PASS
Release Gate                    PASS
Deployment Authorization        PASS
Execution Integrity Gate        PASS
one-shot Vercel PREPARE         PASS
autoAssignCustomDomains=false   PASS
candidate READY                 PASS
prepare_write_count=1           PASS
third human PROMOTE decision    PASS
provider promotion              NOT PERFORMED
Production traffic change       NONE
real project target             NEVER USED
cleanup                         PASS
```

Step 4A passing does not authorize Step 5 provider promotion.

## Preview environment refresh

A documentation-only branch update may be used after Preview environment variables
change so Vercel creates a fresh Git-integrated Preview with the new variables.
This does not authorize or invoke the sacrificial PREPARE provider mutation.

<!-- preview-env-refresh: 2026-10-01-step4a-2 -->
