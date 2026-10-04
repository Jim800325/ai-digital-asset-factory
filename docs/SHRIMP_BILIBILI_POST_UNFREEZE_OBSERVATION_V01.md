# Step 10B.17 — Post-Unfreeze Observation Window + Gradual Exposure Ramp + Auto-Refreeze Recommendation + Restore Acceptance

## Purpose

Step 10B.17 changes Safe Unfreeze from an immediate return to full NORMAL
exposure into a staged observation process.

The new restore path is:

    CAUTION / FROZEN
      -> Recovery Evidence Gate
      -> Two-Person Safe Unfreeze Approval
      -> OBSERVATION 25%
      -> OBSERVATION 50%
      -> OBSERVATION 75%
      -> OBSERVATION 100%
      -> Restore Acceptance
      -> NORMAL 100%

Step 10B.16 therefore remains the authorization boundary for leaving
CAUTION/FROZEN, but Step 10B.17 changes its applied destination from immediate
NORMAL to OBSERVATION Stage 1.

## OBSERVATION policy state

Migration 058 adds OBSERVATION to the Reliability Policy Control state set.

Stage 1:

    automation_exposure = OBSERVATION
    quota_multiplier_percent = 25
    new_reservation_allowed = true

The second Safe Unfreeze approver creates the Observation Session
automatically.

No additional provider write is performed by starting observation.

## Ramp stages

Default ramp:

    Stage 1 = 25%
    Stage 2 = 50%
    Stage 3 = 75%
    Stage 4 = 100%

Configuration:

    SHRIMP_BILIBILI_OBSERVATION_STAGE_MINUTES=360
    SHRIMP_BILIBILI_OBSERVATION_MIN_EXECUTIONS=1

Each stage therefore defaults to a minimum six-hour observation window and at
least one new Bilibili execution with a real upload/publish write attempt.

The execution must have:

    upload_write_count > 0
    OR
    publish_write_count > 0

Execution evidence is counted only after the current stage_started_at.

Advancing a stage resets stage_started_at, so each stage requires new evidence.

## Observation health evidence

Every Ramp Evaluation includes the complete Safe Unfreeze Recovery Evidence
Gate plus stage-specific checks.

Global checks include:

- fresh Global Reliability Scorecard
- Reliability Score >= 85
- ACK Error Budget within limit
- Recovery Error Budget within limit
- Burn = HEALTHY
- no OPEN Regression
- no RECURRING Root Cause
- no unresolved Publisher Incident
- all Account Circuits CLOSED
- no active ambiguous Claim
- global Ambiguity Rate <= configured ambiguity target

Stage-specific checks:

- no UPLOAD_UNKNOWN / PUBLISH_UNKNOWN
- no upload/publish AMBIGUOUS outcome
- no UPLOAD_FAILED / PUBLISH_FAILED

The persisted evaluation records:

- stage before
- quota before
- decision
- elapsed stage minutes
- observed execution count
- health evidence snapshot
- health evidence SHA-256
- evaluation SHA-256

Ramp Evaluation history is immutable.

Repeated identical evaluation requests are idempotent.

## Ramp decisions

Possible decisions:

    HOLD
    ADVANCE_TO_50
    ADVANCE_TO_75
    ADVANCE_TO_100
    READY_FOR_ACCEPTANCE
    REFREEZE_RECOMMENDED

### HOLD

Used when health remains good but:

- minimum stage duration has not elapsed
- or minimum execution count is not satisfied

HOLD changes no Policy Control.

### ADVANCE

When time, execution, and health requirements all pass:

    25 -> 50
    50 -> 75
    75 -> 100

The service locks the Observation Session and GLOBAL Policy Control before
updating quota.

Each Advance:

- keeps automation_exposure = OBSERVATION
- keeps new_reservation_allowed = true
- increments control_version
- writes OBSERVATION_RAMP_APPLIED Control Event
- performs zero Bilibili provider writes

Existing Claims and Reservations are unchanged.

## Stage 4 observation

Reaching 100 percent does not mean NORMAL.

Stage 4 must itself remain at 100 percent for the configured observation
duration and produce the configured minimum number of new executions.

Only then:

    READY_FOR_ACCEPTANCE

is produced.

A frozen Restore Acceptance evidence snapshot is created.

## Restore Acceptance Gate

New independent secret:

    SHRIMP_BILIBILI_RESTORE_ACCEPTANCE_KEY

It must be independent from:

- Restore Approval Key
- Restore Apply Key
- Reliability Policy Apply Key
- Reliability Governance Key
- Health Monitor Key
- Incident Ops Key
- Recovery Approval Key
- Live Acceptance Key
- Step 9 Publish Authorization Key
- Step 10 Execution Key
- Human Review Key

Endpoint:

    POST /v1/shrimp-animation/bilibili-post-unfreeze-observation/
         sessions/{session_id}/accept

Only a READY_FOR_ACCEPTANCE Session can be accepted.

Before acceptance the current observation health is recomputed.

If health degraded:

- prepared Acceptance becomes STALE
- Session becomes REFREEZE_RECOMMENDED
- Policy Control is not changed

If health remains healthy:

    automation_exposure: OBSERVATION -> NORMAL
    quota_multiplier_percent: 100 -> 100
    new_reservation_allowed: true -> true
    control_version: +1

The transition writes an immutable RESTORE_ACCEPTED Control Event.

Provider write count remains zero.

## Auto-Refreeze Recommendation

Observation does not automatically refreeze runtime policy.

If any required health signal degrades, the evaluator writes:

    session_status = REFREEZE_RECOMMENDED
    refreeze_recommendation = REFREEZE_RECOMMENDED

with the exact failed checks and health evidence SHA.

The current limited Policy Control remains in place.

There is no direct automatic transition to FROZEN.

## Human governance integration

Step 10B.14 Governance Evidence Snapshot now includes the active
Post-Unfreeze Observation signal.

If:

    refreeze_recommendation = REFREEZE_RECOMMENDED

the next Governance Review deterministically becomes:

    FREEZE_RECOMMENDED

Reason:

    post-unfreeze observation recommends refreeze

Actual refreeze still requires the existing human chain:

    Governance Review
      -> AUTHORIZE_FREEZE_INTENT
      -> Exact Change Plan
      -> Second Human APPLY
      -> FROZEN

Observation can recommend refreeze but cannot execute it.

If a human-applied Reliability Policy Change occurs while an Observation
Session is ACTIVE, REFREEZE_RECOMMENDED, or READY_FOR_ACCEPTANCE, the
Observation Session is marked STALE. Any pending Restore Acceptance is also
marked STALE. A superseded observation window can never continue ramping after
an independently applied policy change.

Concurrent evaluators also fail closed: the Session stage/quota/status is
rechecked under row lock before a ramp change. If another evaluator already
advanced the stage, the second evaluator cannot advance it again.

## Scheduled evaluation

The existing Bilibili Recovery Policy workflow runs at:

    11,41 * * * *

After the normal Recovery Policy call it now invokes:

    POST /internal/shrimp-animation/
         bilibili-post-unfreeze-observation-evaluate

Authorization:

    SHRIMP_BILIBILI_HEALTH_MONITOR_KEY

If no active Observation Session exists the endpoint returns:

    NO_ACTIVE_OBSERVATION

without failing the workflow.

The scheduled evaluator can:

- HOLD
- advance controlled quota
- create REFREEZE_RECOMMENDED
- create READY_FOR_ACCEPTANCE

It cannot:

- accept final restore
- freeze policy
- modify Circuit
- execute provider writes

## Database

Migration:

    058_shrimp_bilibili_post_unfreeze_observation.sql

Tables:

- shrimp_bilibili_post_unfreeze_observation_sessions
- shrimp_bilibili_post_unfreeze_ramp_evaluations
- shrimp_bilibili_restore_acceptances

Policy Control Events gain:

- OBSERVATION_RAMP_APPLIED
- RESTORE_ACCEPTED

## APIs

Read:

- GET /v1/shrimp-animation/bilibili-post-unfreeze-observation
- GET /v1/shrimp-animation/bilibili-post-unfreeze-observation/sessions
- GET /v1/shrimp-animation/bilibili-post-unfreeze-observation/sessions/{session_id}
- GET /v1/shrimp-animation/bilibili-post-unfreeze-ramp-evaluations
- GET /v1/shrimp-animation/bilibili-restore-acceptances

Protected scheduled evaluation:

- POST /internal/shrimp-animation/bilibili-post-unfreeze-observation-evaluate

Final human acceptance:

- POST /v1/shrimp-animation/bilibili-post-unfreeze-observation/sessions/{session_id}/accept

Header:

    X-Shrimp-Bilibili-Restore-Acceptance-Key

## Control Center

Workspace:

    /animation/reliability-review

now shows:

- active Observation Session
- current Stage
- current quota percentage
- minimum stage duration
- required execution count
- stage execution count
- every health check PASS/BLOCKED
- Auto-Refreeze reason
- Ramp Evaluation history
- Restore Acceptance readiness
- Acceptance history

No secret is persisted in browser state.

## Safety invariants

- Safe Unfreeze no longer jumps directly to NORMAL.
- Each ramp stage requires time + fresh execution evidence + healthy signals.
- Stage 4 requires its own observation period before acceptance.
- Auto-Refreeze is recommendation only.
- Refreeze still requires human Governance + second Apply Gate.
- Final NORMAL requires an independent Restore Acceptance Key.
- Observation/ramp/acceptance perform zero Bilibili provider writes.
- Existing Claims/Reservations/Circuit states are never blindly modified.
