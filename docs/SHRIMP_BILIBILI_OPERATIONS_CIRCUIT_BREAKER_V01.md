# Step 10B.9 — Publisher Operations Console + Automatic Recovery Policy + Claim Escalation + Account Circuit Breaker

## Publisher Operations Console

A new Control Center workspace is available at:

    /animation/operations

It displays:

- open Claim Escalations
- account Circuit Breakers
- Automatic Recovery Policy runs
- immutable Circuit Events
- ambiguity and provider-failure scores
- recovery cooldown and evidence state

The console exposes no Force Release and no blind Retry operation.

## Claim Escalation

Stuck CLAIMED executions are escalated by age and repeated ambiguous read-back.

Defaults:

    SHRIMP_BILIBILI_STUCK_CLAIM_MINUTES=30
    SHRIMP_BILIBILI_CLAIM_WARNING_MINUTES=45
    SHRIMP_BILIBILI_CLAIM_CRITICAL_MINUTES=120

Escalation levels:

- INFO
- WARNING
- CRITICAL

Repeated STILL_AMBIGUOUS reconciliation results may escalate a Claim earlier.

When a Claim no longer matches stuck criteria, its open escalation is marked
RESOLVED.

## Consecutive ambiguity and failure scoring

Circuit scores are consecutive, not lifetime totals.

Ambiguity score counts consecutive recent reconciliation outcomes equal to:

    STILL_AMBIGUOUS

The count stops at the most recent non-ambiguous reconciliation.

Provider failure score counts consecutive recent executions ending in:

    UPLOAD_FAILED
    PUBLISH_FAILED

The count stops at the most recent non-failure execution.

The score window is 24 hours.

## Account Circuit Breaker

Default thresholds:

    SHRIMP_BILIBILI_CIRCUIT_AMBIGUITY_THRESHOLD=3
    SHRIMP_BILIBILI_CIRCUIT_PROVIDER_FAILURE_THRESHOLD=3
    SHRIMP_BILIBILI_CIRCUIT_COOLDOWN_MINUTES=60

Circuit states:

    CLOSED
    OPEN
    RECOVERY_PENDING

When an account becomes OPEN or RECOVERY_PENDING, the Health-Aware Router
rejects it before creating a new Reservation.

Quota availability or a HEALTHY Credential Slot cannot bypass the Circuit.

## Automatic open policy

The Circuit automatically opens when either threshold is reached:

- repeated ambiguous provider read-back
- repeated definitive provider failures

Opening a Circuit records an immutable Circuit Event and evidence SHA-256.

No provider write is performed.

## Evidence-based recovery

A Circuit cannot close merely because the cooldown elapsed.

Recovery requires all of the following:

- cooldown elapsed
- consecutive ambiguity score below threshold
- consecutive provider-failure score below threshold
- Credential Slot ACTIVE
- health status HEALTHY
- degradation status NORMAL
- MID MATCH
- publish permission ALLOWED
- health evidence still fresh
- no active ambiguous CLAIMED execution

If cooldown expires but evidence remains insufficient:

    OPEN -> RECOVERY_PENDING

RECOVERY_PENDING remains blocked from Router selection.

Only sufficient evidence may transition:

    OPEN / RECOVERY_PENDING -> CLOSED

## Manual Evaluate Evidence

The Operations Console provides:

    Evaluate Evidence

This action requires the independent Step 10B Live Acceptance Key.

It only evaluates current health/read-back evidence and Circuit state.
It does not upload, publish, replay a write, or force-release a Claim.

## Automatic Recovery Policy

Protected endpoint:

    POST /internal/shrimp-animation/bilibili-recovery-policy

The policy:

1. finds Stuck Claims
2. performs read-back reconciliation only when appropriate
3. rate-limits automatic read-back to avoid repeated probing
4. synchronizes Claim Escalations
5. evaluates account Circuit Breakers
6. records an auditable Recovery Policy Run

Ambiguous outcomes remain CLAIMED and continue consuming concurrency/quota.

The policy never calls Upload or Publish.

## Scheduling

GitHub Actions workflow:

    .github/workflows/bilibili-recovery-policy.yml

Schedule:

    11,41 * * * *

Required repository secrets:

- SHRIMP_BILIBILI_HEALTH_MONITOR_URL
- SHRIMP_BILIBILI_HEALTH_MONITOR_KEY

If they are absent, the scheduled workflow safely skips.

## APIs

Read-only:

- GET /v1/shrimp-animation/bilibili-operations-console
- GET /v1/shrimp-animation/bilibili-claim-escalations
- GET /v1/shrimp-animation/bilibili-circuit-breakers
- GET /v1/shrimp-animation/bilibili-circuit-events
- GET /v1/shrimp-animation/bilibili-recovery-policy-runs

Evidence evaluation:

- POST /v1/shrimp-animation/bilibili-accounts/{account_key}/circuit/evaluate

Protected automatic policy:

- POST /internal/shrimp-animation/bilibili-recovery-policy

## Safety invariants

- No blind provider write retry.
- No Force Release for ambiguous outcomes.
- OPEN and RECOVERY_PENDING accounts cannot receive new Reservations.
- Recovery requires read-only evidence.
- Historical failures alone do not permanently poison an account; scores are
  consecutive and bounded to recent evidence.
- A CLOSED Circuit does not authorize publishing. Step 9, Step 10 and Step 10B
  Gates remain mandatory.
