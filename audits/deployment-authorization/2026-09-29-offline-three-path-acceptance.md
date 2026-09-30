# Deployment Authorization Offline Three-Path Acceptance

Status: PASSED

Date: 2026-09-29
Scope: feature/deployment-authorization-v0.1 only
Production main: unchanged at 5e200643d883e8244037d22f62e5b028dd75e6ba

## CI evidence

GitHub Actions run: 36549105128

All required CI stages passed:

- Python compile
- migrations 001 through 022
- migration set verification
- historical fixture data reset after migration acceptance
- OpenHands CLI verification
- full pytest suite
- production-disabled invariant verification
- runner cleanup

## Controlled three-path database acceptance

The acceptance ran against the GitHub Actions isolated PostgreSQL service and
used real application code and migration 022 database guards.

### Drift / fail-closed path

A Deployment Plan was created from a RELEASE_APPROVED controlled candidate.
The current integrity snapshot was then deliberately changed by replacing the
manifest root SHA-256.

Result:

- AUTHORIZE raised a fail-closed RuntimeError
- blocking reason included `manifest_root_sha256_drift`
- a row was persisted in `deployment_authorization_blocks`
- no authorization decision was written
- plan remained `PENDING_AUTHORIZATION`
- `execution_enabled=false`

### Valid AUTHORIZE path

The exact integrity snapshot was restored and the same immutable plan was
authorized.

Result:

- `plan_status=AUTHORIZED_FOR_DEPLOYMENT`
- exactly one AUTHORIZE decision persisted
- prior block history remained readable
- `execution_enabled=false`
- release candidate `deployment_enabled=false`
- `production_deployment_executed=false`

### Independent REJECT path

A second independent sandbox request, release candidate, review package, and
Deployment Plan were created from the same approved proposal. This keeps the
terminal authorization state isolated without requiring another opportunity
discovery cycle.

Result:

- exactly one REJECT decision persisted
- `plan_status=DEPLOYMENT_REJECTED`
- `execution_enabled=false`
- release candidate `deployment_enabled=false`
- `production_deployment_executed=false`

## Boundary

This CI acceptance does not replace the real Vercel Preview + isolated Neon
acceptance. Integrity Registry results were deterministically stubbed only for
the CI-controlled external provenance boundary. Migration 022, PostgreSQL
triggers, immutable plan binding, decision persistence, block persistence, and
terminal state transitions were real.

The Controlled Production Release Executor remains absent.

Next required stage:

Vercel Preview -> PREVIEW_DATABASE_URL isolated Neon branch -> migrations
001-022 -> real registry/provenance Deployment Plan -> drift block -> AUTHORIZE
-> REJECT -> cleanup.


## Post-acceptance integrity hardening

After the three-path acceptance passed, the Release Integrity Gate was tightened
so a matching repository audit is not sufficient merely because it is PASSED
and hash-chain VERIFIED.

A matching audit must now also satisfy the Controlled Live Acceptance contract:

- gateway_mode = PROXY
- live_model_verified = true
- budget_status = WITHIN_BUDGET
- tests_passed = true
- external_side_effects = DENY
- release_approved must not be true
- source_commit must equal deployment_source_commit

This prevents a synthetic or MOCK-only PASSED audit from unlocking Release or
Deployment Authorization.

Validation GitHub Actions run: 36549648042

Result: SUCCESS. Full test suite, migrations 001-022, three-path Deployment
Authorization acceptance, and production-disabled invariants all passed after
the integrity hardening.


## Preview Live Acceptance Bridge hardening

A Preview-only bridge was added so the real Vercel Controlled Live Acceptance
can persist exact artifact bytes into the isolated Preview database before the
disposable sandbox is destroyed.

The bridge is fail-closed and requires:

- VERCEL_ENV = preview
- deployment_authorization_preview_only = true
- database source = PREVIEW_DATABASE_URL
- PASSED Controlled Live Acceptance
- gateway_mode = PROXY
- live_model_verified = true
- budget_status = WITHIN_BUDGET
- tests_passed = true
- external_side_effects = DENY
- deployment_enabled = false
- release_approved = false
- exact artifact byte/hash/source-tree equality

It creates two independent strict fixtures:

- AUTHORIZE acceptance candidate
- REJECT acceptance candidate

Both stop at READY_FOR_REVIEW and require normal Release Gate approval after
repository Registry provenance is persisted. The bridge never writes a Release
APPROVE decision, never creates a Deployment Plan, and never executes
deployment.

A separate PREVIEW_ACCEPTANCE_KEY is required; HUMAN_DEPLOYMENT_KEY is not
reused for paid Live Acceptance execution.

Validation GitHub Actions run: 36553412281

Result: SUCCESS.

- full pytest suite passed
- migrations 001-022 passed
- strict artifact content snapshots passed
- both Review Packages were content_snapshot_complete=true
- global build execution count with execution_enabled=true = 0
- global Release Candidate deployment_enabled=true count = 0
- global Deployment Plan execution_enabled=true count = 0
- sandbox external_side_effects outside DENY count = 0

No Live model call occurred during this CI bridge validation.


## Preview redeploy trigger

A feature-branch-only redeploy marker was added on 2026-09-30 after the Vercel
build-rate-limit was reported cleared. This commit changes no runtime logic and
must create Preview only. Production main, Production database, and Production
alias remain frozen.


## Preview database URL correction redeploy

A feature-branch-only redeploy marker was added after PREVIEW_DATABASE_URL was
corrected. This commit changes no runtime logic. It exists only to create a new
Preview deployment that receives the updated Preview-only environment
variables. Production remains unchanged.


## Isolated Neon child branch redeploy

The Preview-only PREVIEW_DATABASE_URL was replaced with the pooled connection
string for the dedicated Neon child branch preview-deployment-auth. A new
feature-branch Preview deployment is triggered so that isolated connection is
injected. Production main, Production Neon, and Production alias remain frozen.
