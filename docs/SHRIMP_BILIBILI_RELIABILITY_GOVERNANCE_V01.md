# Step 10B.14 — Reliability Governance Gate + Change Freeze Recommendation + Human Policy Decision

## Purpose

Step 10B.14 turns the Step 10B.13 reliability analysis into a human governance
review layer.

The automated system may recommend:

- NORMAL
- CAUTION
- FREEZE_RECOMMENDED

It cannot itself:

- freeze publishing
- reduce or increase account quota
- change SLO thresholds
- change Circuit state
- change Reservation / Claim state
- change Step 9 / Step 10 / Step 10B gates
- apply provider or account policy changes

## Governance evidence snapshot

Each Governance Review freezes the current global reliability evidence:

- latest Reliability Scorecard ID / SHA
- Reliability Score / Grade
- ACK / Recovery Error Budget remaining
- Ambiguity rate
- Circuit open count
- recurring Root Cause count
- latest Burn evaluation / SHA
- short / long ACK Burn
- short / long Recovery Burn
- open Regression IDs / severity / SHA
- WATCH / RECURRING Root Cause fingerprints
- open Reliability Policy Recommendation IDs / priorities / SHA

The complete snapshot receives a SHA-256.

Only one PENDING_DECISION Governance Review may exist at a time.

When new evidence produces a different SHA, the previous pending Review is
marked STALE and a new Review must be generated.

A human decision also performs evidence-drift preflight. If the underlying
evidence changed, the Review is persistently marked STALE and the decision is
rejected.

## Recommendation rules

FREEZE_RECOMMENDED is emitted when any of the following is true:

- Burn status is FAST_BURN or EXHAUSTED
- a CRITICAL Reliability Regression is open
- a CRITICAL Reliability Policy Recommendation exists
- a RECURRING Root Cause contains at least two CRITICAL occurrences
- Reliability Score is below 60

CAUTION is emitted when there is no freeze signal, but one or more of:

- Burn status is WATCH
- any Reliability Regression is open
- any Root Cause is RECURRING
- Reliability Score is below 85
- a HIGH or MEDIUM Reliability Policy Recommendation exists

NORMAL is emitted when no current governance escalation signal exists.

These rules are deterministic. No LLM determines governance state.

## Independent Human Governance Gate

New secret:

    SHRIMP_BILIBILI_RELIABILITY_GOVERNANCE_KEY

It must be independent from:

- Incident Ops Key
- Recovery Approval Key
- Step 10B Live Acceptance Key
- Step 9 Publish Authorization Key
- Step 10 Publisher Execution Key
- Shrimp Human Review Key

The key is supplied only in the decision request and is not persisted in the UI.

## Human decisions

Decision compatibility is strict.

For NORMAL:

- ACCEPT_NORMAL
- REJECT_RECOMMENDATION

For CAUTION:

- ACCEPT_CAUTION
- REJECT_RECOMMENDATION

For FREEZE_RECOMMENDED:

- AUTHORIZE_FREEZE_INTENT
- REJECT_RECOMMENDATION

A decision is immutable.

## Policy Intent

Every human decision creates one immutable Policy Intent.

Intent types:

- NO_CHANGE
- CAUTION_CONTROLS
- FREEZE_CHANGE_INTENT
- RECOMMENDATION_REJECTED

Important boundary:

    intent_status = AUTHORIZED_NOT_EXECUTABLE
    execution_enabled = false
    changes_applied = false

AUTHORIZE_FREEZE_INTENT therefore means:

- human accepts that a freeze/change should be considered
- a later controlled policy-execution stage may consume this intent
- this stage does not execute any freeze or configuration change

Requested changes are descriptive only:

- automation exposure review
- publish quota review
- policy change review

No Apply Intent endpoint exists.

## Database

Migration:

    055_shrimp_bilibili_reliability_governance.sql

Tables:

- shrimp_bilibili_reliability_governance_reviews
- shrimp_bilibili_reliability_governance_decisions
- shrimp_bilibili_reliability_policy_intents

Decision and Intent records are immutable.

## Review Workspace

Workspace:

    /animation/reliability-review

The page displays:

- current Recommendation
- Review status
- Evidence SHA
- Score / Grade
- Burn status and rates
- Regression count
- Recurrence signals
- Policy Recommendation count
- Human Decision actions
- Decision history
- Policy Intent history
- Review history

There is no Apply Change, Freeze Now, Change Quota, or Change SLO button.

## APIs

Read:

- GET /v1/shrimp-animation/bilibili-reliability-governance
- GET /v1/shrimp-animation/bilibili-reliability-governance/reviews
- GET /v1/shrimp-animation/bilibili-reliability-governance/reviews/{review_id}
- GET /v1/shrimp-animation/bilibili-reliability-governance/decisions
- GET /v1/shrimp-animation/bilibili-reliability-governance/intents

Human decision:

- POST /v1/shrimp-animation/bilibili-reliability-governance/reviews/{review_id}/decision

Header:

    X-Shrimp-Bilibili-Reliability-Governance-Key

Protected generation:

- POST /internal/shrimp-animation/bilibili-reliability-governance-review

Generation uses the existing Bilibili Health Monitor Key or CRON_SECRET model.

## Daily workflow

The reliability workflow now runs:

1. generate 30-Day Reliability Scorecard
2. generate Trend / Burn / Regression / Policy Recommendations
3. generate Governance Review

This means the human Governance Workspace always reviews persisted evidence,
not ad-hoc browser calculations.

## Safety invariants

- Automated analysis can recommend FREEZE but cannot freeze.
- Human governance decision cannot directly execute changes.
- Policy Intent is explicitly non-executable.
- Evidence drift invalidates a pending human decision.
- Governance Key cannot replace publish, execution, recovery, or live keys.
- Step 9 / Step 10 / Step 10B remain authoritative for publishing.
