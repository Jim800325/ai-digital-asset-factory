# Step 10B.18 — Post-Restore Reliability Certification + Stability Baseline + Long-Term SLO Promotion + Reopen Trigger

## Purpose

A successful Restore Acceptance is not permanent proof of health.

Step 10B.18 creates a formal reliability certification after restoration,
freezes an immutable stability baseline, promotes the current reliability
targets into a long-term SLO contract, and continuously evaluates deterministic
reopen triggers.

The lifecycle becomes:

    Restore Acceptance
      -> NORMAL 100%
      -> Post-Restore Reliability Certification
      -> Frozen Stability Baseline
      -> Promoted Long-Term SLO
      -> Scheduled Reopen Evaluation
      -> STABLE
         or
         REOPEN_RECOMMENDED
      -> Human Governance Review

Certification never performs a Bilibili provider write and never changes
runtime policy by itself.

## Certification eligibility

A Certification can be issued only when all of the following are true:

- Observation Session status = ACCEPTED
- Restore Acceptance status = ACCEPTED
- Reliability Policy Control = NORMAL
- quota multiplier = 100
- new Reservation = allowed
- Global Reliability Scorecard exists
- Burn status = HEALTHY
- zero open Reliability Regressions
- zero recurring Root Causes
- zero unresolved Publisher Incidents
- all account Circuits are CLOSED
- Reliability Score >= long-term minimum
- ACK success satisfies promoted ACK SLO
- Recovery success satisfies promoted Recovery SLO
- Ambiguity rate satisfies promoted ambiguity SLO

Restore Acceptance itself is not rolled back when certification eligibility
fails. The certification cycle can retry later after new reliability evidence
is available.

## Stability Baseline

Certification freezes the actual accepted operating state:

- Reliability Score / Grade
- ACK success rate
- Recovery success rate
- Ambiguity rate
- average / P95 ACK
- average / P95 MTTR
- ACK Error Budget remaining
- Recovery Error Budget remaining
- Circuit open count
- recurring Root Cause count
- Publisher execution count
- source Scorecard SHA
- source Burn evidence SHA

The baseline receives its own SHA-256 and is immutable.

## Promoted Long-Term SLO

The promoted SLO is frozen from the currently configured formal reliability
targets at certification time.

It contains:

- ACK success target
- Recovery success target
- Ambiguity maximum
- Reliability Score minimum = 85
- Burn required = HEALTHY
- Open Regression maximum = 0
- recurring Root Cause maximum = 0
- nonclosed Circuit maximum = 0
- unresolved Incident maximum = 0

Promotion does not mutate application configuration. It records the exact SLO
contract used to certify this restored state.

A later configuration change therefore cannot silently rewrite the historical
Certification.

## Reopen Policy

Every Certification contains a deterministic Reopen Policy.

Default score triggers:

    reliability_score_floor =
      max(85, certified_score - 10)

    reliability_score_drop_points = 10

Other triggers:

- ACK success below promoted ACK target
- Recovery success below promoted Recovery target
- Ambiguity above promoted maximum
- Burn not HEALTHY
- any OPEN Regression
- any RECURRING Root Cause
- any OPEN / RECOVERY_PENDING Circuit
- any unresolved Publisher Incident

Trigger codes include:

- SCORECARD_MISSING
- RELIABILITY_SCORE_BELOW_FLOOR
- RELIABILITY_SCORE_REGRESSION
- ACK_SLO_BREACH
- RECOVERY_SLO_BREACH
- AMBIGUITY_SLO_BREACH
- ERROR_BUDGET_BURN
- OPEN_REGRESSION
- ROOT_CAUSE_RECURRENCE
- CIRCUIT_REOPENED
- INCIDENT_REOPENED

## Reopen Evaluation

Every evaluation freezes:

- Certification SHA
- Baseline SHA
- current Reliability evidence
- current evidence SHA
- trigger codes
- evaluation status
- evaluation SHA

Possible status:

    STABLE
    REOPEN_RECOMMENDED

Evaluation history is immutable and idempotent for identical evidence.

## Governance Reopen Event

When one or more Reopen triggers fire:

    Certification -> REOPEN_RECOMMENDED

and one OPEN Certification Reopen Event is created.

The event contains:

- Certification ID / key
- Evaluation ID
- Trigger codes
- current evidence SHA
- Event SHA
- opened timestamp

The same active Certification can have only one OPEN Reopen Event.

A notification is queued with:

    notification_type = RELIABILITY_CERTIFICATION_REOPEN

Severity is CRITICAL when the triggers include:

- Error Budget burn
- Circuit reopened
- Incident reopened
- Root Cause recurrence

Otherwise the notification is WARNING.

## Human Governance integration

Certification Reopen is added to the Step 10B.14 Governance Evidence Snapshot.

An OPEN Certification Reopen Event makes the next Governance recommendation at
least:

    CAUTION

with reason:

    post-restore certification reopened; human governance review required

Existing stronger evidence continues to take precedence.

For example Fast Burn, Critical Regression, repeated critical Root Cause, or
Post-Unfreeze Refreeze signal can still produce:

    FREEZE_RECOMMENDED

Certification Reopen therefore reopens Governance review but never executes a
freeze itself.

Any actual CAUTION/FREEZE change still requires the existing chain:

    Governance Review
      -> Human Governance Decision
      -> Policy Intent
      -> Exact Change Plan
      -> Second Human Apply Gate

## No automatic self-heal

Once a Certification has entered:

    REOPEN_RECOMMENDED

a later STABLE evaluation does not automatically return it to CERTIFIED and
does not close the OPEN Reopen Event.

The previous Certification represents a historical baseline that has been
violated.

Returning to a certified state requires a new valid restore / acceptance /
certification cycle. The new Certification supersedes the old Certification
and its open Reopen Event.

## Certification cycle

The protected daily Reliability workflow now runs:

1. Generate Reliability Scorecard
2. Generate Trend / Burn / Regression / Policy Recommendations
3. Run Post-Restore Certification Cycle
4. Generate Governance Review

Certification Cycle behavior:

- if an ACCEPTED Restore has no Certification, retry Certification generation
- if an active Certification exists, run Reopen Evaluation
- if Reopen is detected, persist Reopen Event and queue notification
- perform zero provider writes
- perform zero automatic Policy changes

Protected endpoint:

    POST /internal/shrimp-animation/bilibili-post-restore-certification-cycle

Authorization uses the existing Health Monitor Key / CRON_SECRET model.

## Restore Acceptance integration

The final Restore Acceptance attempts immediate Certification after the
OBSERVATION 100% -> NORMAL transition.

Response includes:

    certification
    certification_error

A certification eligibility failure does not falsify or roll back an already
valid Restore Acceptance. Scheduled Certification Cycle can retry later.

## Database

Migration:

    059_shrimp_bilibili_post_restore_certification.sql

Tables:

- shrimp_bilibili_post_restore_certifications
- shrimp_bilibili_certification_reopen_evaluations
- shrimp_bilibili_certification_reopen_events

Certification evidence / baseline / SLO / reopen policy are immutable.

Reopen Evaluation history is immutable.

Reopen Event evidence is immutable.

Allowed Certification state transitions:

    CERTIFIED -> REOPEN_RECOMMENDED
    CERTIFIED -> SUPERSEDED
    REOPEN_RECOMMENDED -> SUPERSEDED

No transition exists from REOPEN_RECOMMENDED back to CERTIFIED.

## APIs

Read:

- GET /v1/shrimp-animation/bilibili-post-restore-certification
- GET /v1/shrimp-animation/bilibili-post-restore-certifications
- GET /v1/shrimp-animation/bilibili-post-restore-certifications/{certification_id}
- GET /v1/shrimp-animation/bilibili-certification-reopen-evaluations
- GET /v1/shrimp-animation/bilibili-certification-reopen-events

Protected cycle:

- POST /internal/shrimp-animation/bilibili-post-restore-certification-cycle

## Control Center

Workspace:

    /animation/reliability-review

now shows:

- active Certification Key / Status
- Certification SHA
- Baseline SHA
- certified timestamp
- frozen Stability Baseline
- Promoted Long-Term SLO
- Reopen Policy
- Reopen Evaluation history
- OPEN / historical Reopen Events
- trigger codes
- evidence SHA

The Certification surface is read-only.

There is no:

- Auto Close Reopen
- Auto Freeze
- Change Quota
- Change Circuit
- Provider Retry

button.

## Safety invariants

- Restore Acceptance is not treated as permanent health.
- Historical Certification evidence cannot be rewritten.
- Long-term SLO contract cannot drift with future configuration.
- Reopen triggers are deterministic.
- Reopen produces evidence + notification + human Governance review.
- Reopen cannot automatically change Policy Control.
- Reopen cannot automatically freeze.
- A reopened Certification cannot auto-heal itself.
- New Certification supersedes old certification only through a new accepted
  restore lifecycle.
- Certification / evaluation performs zero Bilibili provider writes.
