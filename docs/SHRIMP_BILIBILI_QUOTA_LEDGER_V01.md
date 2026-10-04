# Step 10B.7 — Reservation Lifecycle + Execution Claim + Quota Ledger

## Lifecycle

The Bilibili account allocation lifecycle is now:

    HELD
      -> CONSUMED        (routed Step 9 Plan created)
      -> CLAIMED         (controlled Execution created)
      -> PUBLISHED       (publish confirmed/reconciled)
      -> SETTLED         (cleanup verified)

Terminal pre-execution release paths:

    HELD -> EXPIRED
    CONSUMED -> EXPIRED
    CONSUMED -> RELEASED (Plan REJECTED or STALE)

Definitive provider failure after Execution Claim:

    CLAIMED -> RELEASED

Ambiguous upload/publish outcomes do not release the Claim. Reconciliation must
first determine the provider state.

## Pending authorization expiry

Reservation TTL remains active while a routed Plan is
PENDING_AUTHORIZATION.

Default:

    SHRIMP_BILIBILI_RESERVATION_TTL_MINUTES=15

Before Step 9 AUTHORIZE the application expires stale reservations. An expired
reservation causes authorization drift and blocks the decision.

Once the Plan is PUBLISH_AUTHORIZED, this reservation TTL no longer reclaims
the allocation; the Execution Claim lifecycle takes over.

## Execution Claim

Creating a Bilibili controlled execution atomically creates one
shrimp_bilibili_execution_claims row when the Plan has a routed reservation.

The Claim freezes:

- reservation
- execution
- account
- credential slot
- target
- plan/reservation SHA binding
- one quota unit

Only one CLAIMED execution may exist per account.

## Quota Ledger

shrimp_bilibili_quota_ledger is append-only and protected from UPDATE/DELETE.

Ledger entry types:

- CLAIM_CREATED
- PLAN_RELEASED
- RESERVATION_EXPIRED
- PUBLISH_COMMITTED
- CLEANUP_SETTLED
- CLAIM_RELEASED

PUBLISH_COMMITTED carries quota_units=1. All lifecycle/audit events carry
quota_units=0.

Daily quota does not decrease after cleanup. Cleanup proves the sacrificial
publication was removed, but a provider publish already occurred and therefore
still consumed that day's publish allowance.

## Router quota calculation

Account availability is now computed from the ledger/lifecycle rather than
counting only Publisher Execution rows:

    published_units
      = today's PUBLISH_COMMITTED ledger units

    held_units
      = HELD + CONSUMED reservations

    claimed_units
      = CLAIMED execution claims

    available_units
      = daily_publish_limit
        - published_units
        - held_units
        - claimed_units

The account timezone controls the quota date.

## Concurrency

A partial unique index prevents more than one HELD/CONSUMED reservation for an
account.

A separate unique index prevents more than one CLAIMED Execution Claim for an
account.

The job reservation index remains active across HELD, CONSUMED, CLAIMED and
PUBLISHED to prevent a second routed lifecycle from being created while the
first is still operational.

## Plan terminal behavior

When a routed Plan transitions to PUBLISH_REJECTED or STALE before an Execution
Claim exists, its CONSUMED reservation is automatically changed to RELEASED.

The release is recorded by a database trigger as an immutable PLAN_RELEASED
ledger event, so upstream invalidation triggers cannot bypass audit logging.

## Provider outcomes

Definitive UPLOAD_FAILED or PUBLISH_FAILED:
- Execution Claim -> RELEASED
- Reservation -> RELEASED
- CLAIM_RELEASED ledger entry
- no daily publish unit is charged

Ambiguous upload/publish outcome:
- Claim remains active
- no quota release occurs
- reconciliation is mandatory

Confirmed or reconciled publish:
- Claim -> PUBLISHED
- Reservation -> PUBLISHED
- exactly one PUBLISH_COMMITTED ledger unit

Verified Step 10B cleanup:
- Claim -> SETTLED
- Reservation -> SETTLED
- CLEANUP_SETTLED ledger audit entry
- published daily quota remains charged

## Read APIs

- GET /v1/shrimp-animation/bilibili-quota/{account_key}
- GET /v1/shrimp-animation/bilibili-quota-ledger
- GET /v1/shrimp-animation/bilibili-execution-claims

These endpoints expose lifecycle metadata and hashes only. They never expose
Bilibili credential values.

## Safety boundary

The ledger changes account scheduling state only.

It does not authorize Step 9, Step 10 or Step 10B provider writes.

A different account still requires a new routed Reservation, a new Publish
Plan, and a new Step 9 human authorization.
