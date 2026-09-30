# Deployment Authorization Preview Acceptance v0.1

Status: PREVIEW-ONLY / NOT PRODUCTION-READY

This phase validates authorization to deploy. It does not implement, expose, or
enable a production deployment executor.

## Safety boundary

The following must remain true for the entire acceptance:

- Production branch remains on the Release Gate stable baseline.
- Preview runs from `feature/deployment-authorization-v0.1`.
- Preview uses an isolated Preview database / Neon branch.
- Production database must not receive migration 022 during this acceptance.
- `deployment_enabled=false`.
- `execution_enabled=false`.
- `deployment_executor=DISABLED`.
- No Vercel deploy/promote/rollback executor endpoint exists.
- No background worker may execute production deployment.
- `AUTHORIZED_FOR_DEPLOYMENT` is an authorization record only.

## Preview deployment prerequisites

1. Vercel build rate limit has cleared.
2. Create a Preview Deployment from:
   `feature/deployment-authorization-v0.1`.
3. Bind Preview-only database credentials through `PREVIEW_DATABASE_URL`.
   - Vercel Preview must not use `DATABASE_URL`.
   - If `PREVIEW_DATABASE_URL` is absent, application startup must fail closed.
   - The selected database source must report `PREVIEW_DATABASE_URL`.
   - The Preview database must be an isolated Neon branch/database, not Production.
4. Configure Preview-only:
   - `HUMAN_DEPLOYMENT_KEY`
   - existing non-production test keys as required.
5. Do not attach the Production alias.
6. Do not modify Production environment variables.
7. Preview-only code guard must refuse startup when `VERCEL_ENV=production`.

## Migration acceptance

Apply repository migrations 001 through 022 to the isolated Preview database.

Required:

- migration status CURRENT
- expected_count = 22
- applied_count = 22
- latest_version = `022_deployment_authorization_gate.sql`
- no psycopg placeholder error
- no startup error when `PREVIEW_DATABASE_URL` is correctly configured
- startup fails closed when `PREVIEW_DATABASE_URL` is absent
- feature code refuses `VERCEL_ENV=production`
- `deployment_plans` exists
- `deployment_authorization_decisions` exists
- `deployment_authorization_blocks` exists

Database hard boundaries:

- `deployment_plans.execution_enabled` defaults false
- CHECK requires `execution_enabled=false`
- trigger rejects a plan unless the Release Candidate is RELEASE_APPROVED
- trigger requires persisted APPROVE Release Decision binding
- trigger requires GENERATED + complete immutable Review Package
- terminal deployment authorization cannot be changed
- deployment plan provenance snapshot cannot be mutated

## Controlled candidate

Create a Preview-only controlled candidate satisfying:

- release_status = RELEASE_APPROVED
- persisted Release APPROVE decision exists
- immutable Review Package is GENERATED
- content_snapshot_complete = true
- Evidence Integrity Gate = VERIFIED
- Review Package and Release Decision hashes match
- deployment_enabled = false
- execution_enabled = false
- candidate is not archived

No live LLM call is required solely for this authorization acceptance if an
already verified Preview fixture can be deterministically created.

## Deployment Plan acceptance

Using the Preview-only `HUMAN_DEPLOYMENT_KEY`:

1. Create an immutable Deployment Plan.
2. Bind target provider = VERCEL.
3. Bind target environment = production as plan metadata only.
4. Bind a specific target project/team ID.
5. Persist:
   - Release Candidate ID
   - Release Decision ID
   - Review Package ID
   - Review Package SHA-256
   - Review source-tree SHA-256
   - acceptance provenance tree SHA-256
   - Live Acceptance Audit ID
   - evidence SHA-256
   - audit chain SHA-256
   - manifest root
   - chain head
   - source commit
   - deployment source commit
   - source Vercel deployment ID
6. Generate deterministic `plan_sha256`.

Creating the plan must produce no external side effect.

## Fail-closed authorization acceptance

Deliberately cause exactly one controlled mismatch, such as:

- wrong supplied plan SHA, or
- provenance drift in a Preview-only fixture, or
- archived candidate, or
- Review Package binding mismatch.

Attempt AUTHORIZE.

Required result:

- HTTP 409 / fail-closed
- `deployment_authorization_blocks` persists the event when the mismatch is a
  revalidation drift condition
- no AUTHORIZE decision is written
- plan remains PENDING_AUTHORIZATION
- execution_enabled remains false
- production_deployment_executed remains false
- no Vercel deployment action occurs

Restore the fixture after the blocked-path test.

## Successful authorization acceptance

With all bindings restored and VERIFIED:

1. Human enters `HUMAN_DEPLOYMENT_KEY`.
2. Human confirms AUTHORIZE.
3. Backend revalidates current Release / Review / Integrity provenance.
4. Persist exactly one AUTHORIZE decision.

Required result:

- plan_status = AUTHORIZED_FOR_DEPLOYMENT
- terminal authorization decision is persisted
- execution_enabled = false
- release candidate deployment_enabled = false
- production_deployment_executed = false
- deployment_executor = DISABLED
- no new Vercel deployment exists as a consequence of authorization

## Reject-path acceptance

Use a separate Preview fixture / plan because authorization is terminal.

Required:

- REJECT persists once
- plan_status = DEPLOYMENT_REJECTED
- execution_enabled remains false
- no deployment action occurs

## Cleanup

After acceptance:

- archive Preview-only fixtures
- retain read-only acceptance summary
- remove any TEST_ONLY exception added solely for acceptance
- keep production executor absent
- Preview runtime errors after cleanup = 0
- Production remains unchanged on the Release Gate stable baseline

## Exit criteria

This phase passes only if all three paths are verified:

1. drift / mismatch -> fail-closed
2. AUTHORIZE -> AUTHORIZED_FOR_DEPLOYMENT
3. REJECT -> DEPLOYMENT_REJECTED

And in every path:

```text
deployment_enabled = false
execution_enabled = false
deployment_executor = DISABLED
production_deployment_executed = false
```

Only after this document is fully satisfied may a separate project phase design
the Controlled Production Release Executor.
