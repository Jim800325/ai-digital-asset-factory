# Step 10B.8 — Quota Dashboard + Stuck Claim Reconciliation + Daily Reset Audit

## Control Center

A new workspace is available at:

    /animation/quota

It shows:

- per-account daily publish limit
- published units for the account-local date
- HELD / CONSUMED reservation occupancy
- CLAIMED execution occupancy
- available units
- Reservation lifecycle
- Execution Claim lifecycle
- immutable Quota Ledger
- Stuck Claims
- Daily Reset Audit history

No credential values are returned.

## Stuck Claim detection

A CLAIMED execution is considered stuck when either:

- claimed_at is older than SHRIMP_BILIBILI_STUCK_CLAIM_MINUTES
- execution status is UPLOADING / UPLOAD_UNKNOWN
- execution status is PUBLISHING / PUBLISH_UNKNOWN

Default:

    SHRIMP_BILIBILI_STUCK_CLAIM_MINUTES=30

The dashboard never exposes a Force Release operation.

## Stuck reconciliation

Allowed operations are read-only provider reconciliation only:

- UPLOADING / UPLOAD_UNKNOWN -> upload read-back reconciliation
- PUBLISHING / PUBLISH_UNKNOWN -> publish read-back reconciliation

If provider state remains ambiguous, the Claim remains CLAIMED and continues to
occupy the account. No quota is released.

The reconciliation operation requires the independent Step 10B Live Acceptance
key.

Each reconciliation creates an immutable audit row containing:

- execution / claim / account binding
- execution state before and after
- selected read-back action
- reconciliation outcome
- claim state after
- evidence SHA-256
- provider_write_performed=false

## Daily Reset Audit

Daily quota is date-based; there is no destructive reset operation.

Instead, the system creates one immutable audit snapshot per account per local
quota date.

The audit records:

- daily publish limit
- current-day PUBLISH_COMMITTED units
- HELD / CONSUMED occupancy
- CLAIMED occupancy
- available units
- previous-day published units
- claims carried across local midnight
- reservations carried across local midnight
- audit status

Audit states:

- BALANCED
- CARRYOVER_PRESENT
- OVER_LIMIT
- INCONSISTENT

A unique account/date constraint makes the snapshot idempotent.

## Scheduling

GitHub Actions runs the protected audit endpoint hourly:

    .github/workflows/bilibili-daily-quota-audit.yml

The endpoint is idempotent, so the first successful run after an account's local
date changes records that day's reset-boundary snapshot.

Required repository secrets:

- SHRIMP_BILIBILI_HEALTH_MONITOR_URL
- SHRIMP_BILIBILI_HEALTH_MONITOR_KEY

If either is absent, the scheduled workflow safely skips.

## APIs

Read-only:

- GET /v1/shrimp-animation/bilibili-quota-dashboard
- GET /v1/shrimp-animation/bilibili-stuck-claims
- GET /v1/shrimp-animation/bilibili-stuck-reconciliations
- GET /v1/shrimp-animation/bilibili-daily-quota-audits
- existing Reservation / Claim / Quota Ledger read APIs

Controlled read-back reconciliation:

- POST /v1/shrimp-animation/bilibili-stuck-claims/{execution_id}/reconcile

Protected scheduled audit:

- POST /internal/shrimp-animation/bilibili-daily-quota-audit

## Safety invariants

- No Force Release for ambiguous provider outcomes.
- PUBLISH_UNKNOWN keeps its Claim and quota occupancy until reconciliation.
- Daily Reset Audit never deletes or rewrites ledger entries.
- Cleanup does not refund PUBLISH_COMMITTED units.
- Reconciliation does not replay provider writes.
- Account switching still requires a new Reservation, Plan and Step 9 human
  authorization.
