# Controlled Production Release Executor v0.1 — Step 3 Human Production Execution Gate

Status: **IMPLEMENTATION STEP 3 — MOCK ONLY / NO PRODUCTION WRITES**

This step adds the third independent human gate after a candidate is prepared and verified.

## Independent credential

The decision endpoint requires `X-Production-Execution-Key`, backed by `HUMAN_PRODUCTION_EXECUTION_KEY`.

The key fails closed when it is missing or equals any earlier Approval, Release, or Deployment Authorization key.

## Decision request

The request accepts only:

- `decision`: `PROMOTE` or `ABORT`
- `reason`
- `actor`
- `execution_sha256`

The request deliberately cannot supply a candidate deployment ID, candidate URL, target project/team, source commit, or artifact bytes.

## Immutable server-side binding

Every decision is bound to:

- execution ID and execution SHA-256
- Deployment Plan ID and Plan SHA-256
- persisted candidate deployment ID and URL
- target project/team
- persisted `CANDIDATE_VERIFIED` event
- candidate provider-result SHA-256
- Execution Integrity Gate check ID
- deterministic decision SHA-256

Database triggers reject mismatched bindings.

## PROMOTE behavior in Step 3

`PROMOTE` requires `READY_FOR_PROMOTION` and performs a fresh Execution Integrity Gate evaluation in the same locked decision transaction.

If integrity fails, the BLOCKED check is committed and no decision is persisted.

If integrity passes, the immutable human decision is persisted, but the execution remains `READY_FOR_PROMOTION`.

Step 3 explicitly performs no provider write and changes no Production traffic.

## ABORT behavior

`ABORT` is accepted only for a prepared `READY_FOR_PROMOTION` execution. It binds to the latest integrity evidence and safely transitions the execution to `ABORTED`.

ABORT does not invoke any provider operation.

## Idempotency

Exactly one terminal human decision may exist per execution. Repeating the same decision with the same execution SHA returns the persisted decision. A conflicting later decision fails closed.

## Safety invariants

- `CONTROLLED_PRODUCTION_EXECUTOR_ENABLED=false`
- `PRODUCTION_PROMOTION_ENABLED=false`
- `PRODUCTION_ROLLBACK_ENABLED=false`
- `PRODUCTION_EXECUTION_ADAPTER=MOCK`
- `provider_write_performed=false`
- `production_traffic_changed=false`

## Next boundary

After Step 3 CI acceptance, the next implementation phase is a provider PREPARE adapter in a dedicated sacrificial Preview target. It must still avoid real Production traffic and must denylist the real Production project during acceptance.
