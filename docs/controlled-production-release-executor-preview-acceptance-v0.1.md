# Controlled Production Release Executor v0.1 — Preview Acceptance Plan

Status: **DESIGN ONLY — NO REAL PRODUCTION TARGET**

This acceptance plan validates the executor against a dedicated sacrificial
Vercel project. The real Production project is explicitly forbidden during this
phase.

## Required topology

- control-plane code: Preview only
- database: isolated Preview database
- target provider: Vercel
- target project: dedicated sacrificial acceptance project
- real Production project ID: denylisted
- independent `HUMAN_PRODUCTION_EXECUTION_KEY`
- `CONTROLLED_PRODUCTION_EXECUTOR_ENABLED=true` only in the acceptance Preview
- `PRODUCTION_PROMOTION_ENABLED=true` only for the sacrificial project
- real project Promotion remains disabled

## CI acceptance before live provider tests

CI must prove with a MOCK adapter:

1. unauthorized plan cannot create an execution
2. REJECTED plan cannot create an execution
3. archived candidate fails closed
4. Review Package drift fails closed
5. audit mutation/removal fails closed
6. verified append-only audit chain extension is accepted
7. target project/team mismatch fails closed
8. duplicate prepare request creates one execution
9. ambiguous provider write becomes UNKNOWN and is not replayed
10. arbitrary promotion deployment ID is impossible
11. arbitrary rollback target is impossible
12. all secrets are redacted from events
13. DB outage blocks all execution-sensitive writes

## Controlled live Preview paths

### Path A — target denylist

Attempt to construct an execution for the real Production project.

Required:

- HTTP 409 / fail closed
- zero Vercel provider writes
- persisted block/evidence event

### Path B — prepare without traffic

Using the sacrificial target:

1. create execution snapshot
2. materialize immutable bundle
3. prepare with Production configuration but skip domain assignment
4. wait for Vercel READY
5. verify candidate deployment directly

Required:

- status `READY_FOR_PROMOTION`
- Production pointer unchanged
- exact candidate deployment ID persisted
- no alias/domain promotion
- no automatic promotion

### Path C — third human gate / promotion

Persist explicit PROMOTE decision using the independent execution key.

Required:

- exact persisted candidate deployment is promoted
- no alternate deployment ID can be supplied
- previous Production deployment captured before provider write
- resulting Production pointer equals candidate
- Production health passes
- status `PRODUCTION_ACTIVE`

### Path D — ambiguous provider response

Inject an artificial transport timeout after provider acceptance.

Required:

- no automatic second promote
- status becomes `PROMOTION_UNKNOWN`
- reconciliation reads provider state
- if candidate is active, state converges to `PRODUCTION_ACTIVE`
- provider write count remains one

### Path E — rollback

Use a sacrificial release whose post-promotion health check intentionally fails.

Default v0.1 rollback mode is MANUAL_ROLLBACK.

Required:

- `ROLLBACK_REQUIRED`
- rollback target equals captured previous Production deployment
- arbitrary target rejected
- explicit rollback action returns pointer to previous deployment
- status `ROLLED_BACK`

## Cleanup

After acceptance:

- archive acceptance-only execution fixtures
- delete sacrificial deployments when safe
- remove temporary Preview-only execution credentials
- remove any acceptance-only endpoint / injection hook
- retain read-only execution event evidence
- runtime errors after cleanup = 0
- real Production project pointer unchanged throughout the entire acceptance

## Exit criteria

Only after all paths pass may the project state:

```text
CONTROLLED PRODUCTION RELEASE EXECUTOR PREVIEW ACCEPTANCE
— PASSED / PREPARE / PROMOTE / RECONCILE / ROLLBACK / CLEANED UP
```

That still does not authorize a real Production release. Real Production
enablement is a separate operator decision after acceptance.
