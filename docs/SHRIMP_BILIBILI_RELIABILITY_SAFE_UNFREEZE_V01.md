# Step 10B.16 — Policy Rollback Plan + Safe Unfreeze + Two-Person Restore Approval

## Goal

Restore a previously applied Reliability Policy Control from:

- CAUTION -> NORMAL
- FROZEN -> NORMAL

without allowing a one-click unfreeze.

NORMAL cannot enter this restore flow.

## Recovery Evidence Gate

A Restore Plan can be created only when every check passes:

- latest Global Reliability Scorecard exists
- Reliability Score >= 85
- ACK Error Budget consumed <= allowed
- Recovery Error Budget consumed <= allowed
- Burn status = HEALTHY
- no OPEN Reliability Regression
- no RECURRING Root Cause
- no unresolved Publisher Incident
- all Account Circuits are CLOSED
- no active CLAIMED execution remains in UPLOAD_UNKNOWN / PUBLISH_UNKNOWN
- Scorecard evidence is fresh
- Burn evidence is fresh

Freshness defaults to:

    max(24 hours, SHRIMP_BILIBILI_BURN_LONG_WINDOW_HOURS + 12 hours)

With the current 24-hour long Burn window, maximum evidence age is 36 hours.

If any check fails:

    eligible_for_restore = false

and no Restore Plan may be generated.

## Restore Plan

A plan freezes:

- current Policy Control snapshot
- proposed NORMAL snapshot
- Recovery Evidence snapshot
- exact field-level change set
- dry-run diff
- source control SHA-256
- proposed control SHA-256
- Recovery Evidence SHA-256
- Plan SHA-256
- Dry-Run SHA-256

Restore Plan evidence is immutable.

Only plan status/timestamps may advance.

## Proposed NORMAL state

Safe Unfreeze always proposes:

    automation_exposure = NORMAL
    quota_multiplier_percent = 100
    new_reservation_allowed = true
    control_version = current + 1

It cannot use the restore path to create CAUTION or FROZEN.

## Dry-Run

The dry-run records:

- CAUTION/FROZEN -> NORMAL
- quota multiplier -> 100
- new Reservation -> ALLOWED_AFTER_APPLY
- existing Claims -> UNCHANGED
- existing Reservations -> UNCHANGED
- Circuit state -> UNCHANGED
- Publish authorization gates -> UNCHANGED
- provider_write_count = 0
- credential_access_count = 0
- changes_applied = false

## Two-Person Restore Approval

Two new independent secrets are required:

    SHRIMP_BILIBILI_RELIABILITY_RESTORE_APPROVAL_KEY
    SHRIMP_BILIBILI_RELIABILITY_RESTORE_APPLY_KEY

Both must be independent from each other and from prior governance, incident,
recovery, live acceptance, publish authorization, publish execution, and
human review keys.

### Stage 1

First human:

    FIRST_APPROVAL
    APPROVE / REJECT

APPROVE changes plan state:

    PENDING_FIRST_APPROVAL
      ->
    PENDING_SECOND_APPROVAL

No Policy Control change occurs.

### Stage 2

Second human:

    SECOND_APPLY
    APPROVE / REJECT

The second actor must be different from the first actor, compared
case-insensitively after trimming.

A single operator cannot perform both approvals.

## Final Apply Preflight

Before either approval and again before final Apply:

- Plan SHA must match
- Dry-Run SHA must match
- current Policy Control SHA must match frozen source snapshot
- Recovery Evidence Gate must still pass
- Recovery Evidence SHA must match frozen evidence

Any evidence/control drift marks the plan STALE.

The operator must regenerate a new Restore Plan.

## Apply behavior

Only the second APPROVE applies the NORMAL control.

It changes:

    automation_exposure = NORMAL
    quota_multiplier_percent = 100
    new_reservation_allowed = true

It increments control version.

It does not:

- perform provider writes
- release existing Claims
- release existing Reservations
- change Circuit state
- change Incident state
- bypass Step 9 / Step 10 / Step 10B

A SAFE_UNFREEZE_APPLIED immutable Control Event records:

- restore_plan_id
- previous snapshot
- next snapshot
- event SHA
- actor

## Database

Migration:

    057_shrimp_bilibili_reliability_safe_unfreeze.sql

New tables:

- shrimp_bilibili_reliability_restore_plans
- shrimp_bilibili_reliability_restore_approvals

Policy Control Events gain:

- restore_plan_id
- SAFE_UNFREEZE_APPLIED event type

Restore approval history is immutable.

## APIs

Read:

- GET /v1/shrimp-animation/bilibili-reliability-safe-unfreeze
- GET /v1/shrimp-animation/bilibili-reliability-restore-plans
- GET /v1/shrimp-animation/bilibili-reliability-restore-plans/{plan_id}
- GET /v1/shrimp-animation/bilibili-reliability-restore-approvals

Generate plan:

- POST /v1/shrimp-animation/bilibili-reliability-restore-plans
- key: X-Shrimp-Bilibili-Reliability-Governance-Key

First approval:

- POST /v1/shrimp-animation/bilibili-reliability-restore-plans/{plan_id}/first-approval
- key: X-Shrimp-Bilibili-Reliability-Restore-Approval-Key

Second apply:

- POST /v1/shrimp-animation/bilibili-reliability-restore-plans/{plan_id}/second-apply
- key: X-Shrimp-Bilibili-Reliability-Restore-Apply-Key

## Control Center

Workspace:

    /animation/reliability-review

now shows:

- Recovery Evidence Gate
- PASS / BLOCKED for each restore condition
- current exposure
- Recovery Score / Burn
- Restore Plan history
- CAUTION/FROZEN -> NORMAL dry-run
- first approval actions
- second apply actions
- immutable two-person approval audit

Keys are used only for the current request and are not stored in browser state.

## Safety invariants

- NORMAL cannot be restored to NORMAL.
- Evidence must be current and healthy.
- One person cannot perform both approvals.
- First approval cannot change runtime control.
- Second Apply must recheck hashes/evidence.
- Drift makes the plan STALE.
- Existing uncertain work is never blindly released.
- Safe Unfreeze performs zero Bilibili provider writes.
