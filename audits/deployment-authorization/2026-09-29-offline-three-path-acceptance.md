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
