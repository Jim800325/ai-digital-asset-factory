# Step 10B.15 — Controlled Reliability Policy Change Plan + Exact Change Set + Dry-Run Diff + Second Human Apply Gate

## Purpose

Step 10B.15 is the first stage allowed to apply a Reliability Governance
Policy Intent to runtime policy controls.

It does not directly edit Bilibili credentials, account identity, Circuit
state, existing Claims, existing Reservations, SLO thresholds, or Step 9/10
authorization records.

The chain is:

    Governance Review
      -> Human Governance Decision
      -> Policy Intent
      -> Exact Change Plan
      -> Dry-Run Diff
      -> Second Human Apply Gate
      -> Reliability Policy Control

## Independent second Apply Gate

New secret:

    SHRIMP_BILIBILI_RELIABILITY_POLICY_APPLY_KEY

It must be independent from:

- Reliability Governance Key
- Incident Ops Key
- Recovery Approval Key
- Step 10B Live Acceptance Key
- Step 9 Publish Authorization Key
- Step 10 Publisher Execution Key
- Shrimp Human Review Key

Plan creation uses the existing Reliability Governance Key.

Actual APPLY or REJECT uses the new Policy Apply Key.

## Runtime Policy Control

A single GLOBAL control contains:

- automation_exposure
- quota_multiplier_percent
- new_reservation_allowed
- control_version
- source_plan_id

States:

    NORMAL
    CAUTION
    FROZEN

Initial state:

    automation_exposure = NORMAL
    quota_multiplier_percent = 100
    new_reservation_allowed = true

## Deterministic intent mapping

CAUTION_CONTROLS becomes:

    automation_exposure = CAUTION
    quota_multiplier_percent = 50
    new_reservation_allowed = true

FREEZE_CHANGE_INTENT becomes:

    automation_exposure = FROZEN
    quota_multiplier_percent = 0
    new_reservation_allowed = false

NO_CHANGE and RECOMMENDATION_REJECTED do not authorize a Change Plan.

## Exact Change Set

A Plan freezes:

- source Policy Intent
- Governance Review
- Governance Decision
- Governance evidence SHA
- current Policy Control snapshot
- proposed Policy Control snapshot
- exact field-level change set
- dry-run diff
- current control SHA
- proposed control SHA
- plan SHA
- dry-run SHA

The exact change set contains only changed fields.

Example:

    automation_exposure: NORMAL -> FROZEN
    quota_multiplier_percent: 100 -> 0
    new_reservation_allowed: true -> false

## Dry-Run Diff

Dry-run verifies expected runtime effects without applying changes.

It records:

- before snapshot
- after snapshot
- exact changes
- new Reservation behavior
- effective quota multiplier
- existing Claims = UNCHANGED
- existing Reservations = UNCHANGED
- Circuit state = UNCHANGED
- publish authorization gates = UNCHANGED
- external provider write count = 0
- credential access count = 0
- changes_applied = false

## Apply preflight

Second Human APPLY requires the exact:

- plan_sha256
- dry_run_sha256

Before Apply, the service rechecks:

1. Plan is PENDING_APPLY.
2. Human supplied Plan SHA matches.
3. Human supplied Dry-Run SHA matches.
4. current Policy Control SHA still equals frozen current snapshot.
5. current Reliability Governance evidence SHA still equals the Plan evidence.
6. Plan is locked again inside the Apply transaction.
7. current Policy Control is locked again before mutation.

Control or Governance evidence drift marks the Plan STALE.

A stale Plan cannot be applied.

## APPLY behavior

APPLY changes only the GLOBAL Reliability Policy Control.

It increments control_version and records source_plan_id.

It also writes an immutable Policy Control Event.

The Apply response explicitly reports:

    provider_write_count = 0

No Bilibili provider network write is performed.

## REJECT behavior

REJECT writes an immutable Second Human Apply Decision and makes the Plan
STALE.

It does not modify Policy Control.

## Runtime enforcement

The applied Policy Control affects new work only.

### New Reservation

When:

    new_reservation_allowed = false

the router refuses creation of new Bilibili pre-publish Reservations.

Existing Reservations are not released.

Existing Claims are not released.

Existing ambiguous/recovery workflows are not interrupted.

### Quota

Effective daily limit is calculated from:

    account.daily_publish_limit
      x
    reliability quota_multiplier_percent

NORMAL:

    100 percent

CAUTION:

    50 percent

FROZEN:

    0 percent

For a non-zero base limit and non-zero multiplier, the effective limit keeps a
minimum of one unit so CAUTION does not accidentally turn a one-post account
into a full freeze.

Only FROZEN / zero multiplier produces zero effective quota.

Quota API responses expose:

- original daily_publish_limit
- effective_daily_publish_limit
- reliability_quota_multiplier_percent
- automation_exposure

## Reservation provenance

New Reservation selection snapshots include:

- automation_exposure
- quota_multiplier_percent
- new_reservation_allowed
- control_version

This preserves which Reliability Policy version governed account selection.

## Database

Migration:

    056_shrimp_bilibili_reliability_policy_change.sql

Tables:

- shrimp_bilibili_reliability_policy_controls
- shrimp_bilibili_reliability_change_plans
- shrimp_bilibili_reliability_change_apply_decisions
- shrimp_bilibili_reliability_policy_control_events

Plan evidence is immutable.

Second Human Apply Decisions are immutable.

Control Events are immutable.

## APIs

Read:

- GET /v1/shrimp-animation/bilibili-reliability-policy-change
- GET /v1/shrimp-animation/bilibili-reliability-policy-control
- GET /v1/shrimp-animation/bilibili-reliability-change-plans
- GET /v1/shrimp-animation/bilibili-reliability-change-plans/{plan_id}
- GET /v1/shrimp-animation/bilibili-reliability-change-apply-decisions
- GET /v1/shrimp-animation/bilibili-reliability-policy-control-events

Create Plan:

- POST /v1/shrimp-animation/bilibili-reliability-governance/intents/{intent_id}/change-plan

Header:

    X-Shrimp-Bilibili-Reliability-Governance-Key

Second Human decision:

- POST /v1/shrimp-animation/bilibili-reliability-change-plans/{plan_id}/decision

Header:

    X-Shrimp-Bilibili-Reliability-Policy-Apply-Key

Decision:

    APPLY
    REJECT

## Control Center

Workspace:

    /animation/reliability-review

now shows:

- current applied Policy Control
- Policy Intent
- Create Plan action
- exact before / after Exposure
- exact before / after Quota multiplier
- exact before / after Reservation policy
- Plan SHA
- Dry-Run SHA
- APPLY / REJECT second human actions
- immutable Apply Decision history

Neither secret is persisted by the browser.

## Safety invariants

- Intent alone cannot change runtime policy.
- Plan creation cannot change runtime policy.
- Dry-run cannot change runtime policy.
- APPLY requires a second independent human secret.
- Plan SHA and Dry-Run SHA are mandatory.
- Governance evidence drift invalidates Apply.
- Control state drift invalidates Apply.
- FROZEN does not blindly release existing Claims or Reservations.
- No provider write occurs during policy Apply.
- Step 9 / Step 10 / Step 10B remain authoritative for provider publishing.
