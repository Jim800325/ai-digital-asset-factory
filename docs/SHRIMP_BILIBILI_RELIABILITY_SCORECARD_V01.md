# Step 10B.12 — Incident Metrics + SLO/Error Budget + Reliability Scorecard + Recurrence Detection

## Purpose

Step 10B.12 turns the Publisher Incident history into a deterministic
reliability governance layer.

It observes long-term behavior only.

It does not:

- close Circuit Breakers
- release Claims or Reservations
- retry provider writes
- authorize recovery
- authorize publishing

## Rolling window

Default reliability window:

    SHRIMP_BILIBILI_RELIABILITY_WINDOW_DAYS=30

Scorecards are generated for:

- GLOBAL publisher scope
- each ACTIVE Bilibili account

Each scorecard is an immutable snapshot.

## Metrics

The scorecard records:

- Incident count
- Resolved Incident count
- Acknowledged Incident count
- ACK SLO success / breach count
- Recovery SLO success / breach count
- Average ACK minutes
- P95 ACK minutes
- Average MTTR
- P95 MTTR
- Circuit open count
- Provider ambiguity count
- Publisher execution count
- Ambiguity rate
- Recurring Root Cause count

## ACK SLO

Default target:

    SHRIMP_BILIBILI_ACK_SLO_TARGET_PERCENT=95.0

An Incident is an ACK success when:

    acknowledged_at <= ack_due_at

An Incident consumes ACK Error Budget when:

- acknowledgement occurs after ack_due_at
- or ack_due_at has passed and the Incident is still unacknowledged

## Recovery SLO

Default target:

    SHRIMP_BILIBILI_RECOVERY_SLO_TARGET_PERCENT=95.0

Recovery success:

    resolved_at <= recovery_due_at

Recovery budget is consumed when:

- Incident resolves after recovery_due_at
- or recovery_due_at passes before resolution

## Error Budget

Allowed failures use strict SLO math:

    floor(total_evaluable_incidents * allowed_failure_rate)

Example at 95 percent target:

    20 evaluable Incidents -> 1 allowed failure
    1 evaluable Incident   -> 0 allowed failures

The snapshot records:

- allowed failures
- consumed failures
- remaining failures
- success rate

## Provider ambiguity

Default ambiguity target:

    SHRIMP_BILIBILI_AMBIGUITY_TARGET_PERCENT=5.0

Ambiguity events are reconciliation outcomes equal to:

    STILL_AMBIGUOUS

Ambiguity rate is:

    provider_ambiguity_count / publisher_execution_count * 100

## Recurrence Detection

Only COMPLETED Post-Incident Reviews are eligible.

The Root Cause string is normalized using:

1. Unicode NFKC
2. lowercase
3. punctuation -> spaces
4. whitespace collapse

The normalized Root Cause receives a SHA-256 fingerprint.

No LLM or semantic guess is used for recurrence clustering.

This makes the grouping deterministic and auditable.

Default threshold:

    SHRIMP_BILIBILI_RECURRENCE_THRESHOLD=2

Cluster states:

- OBSERVED
- WATCH
- RECURRING
- RESOLVED

With the default threshold:

    1 occurrence -> WATCH
    2+ occurrences -> RECURRING

A cluster records:

- normalized Root Cause
- fingerprint
- Incident IDs
- affected account keys
- occurrence count
- CRITICAL occurrence count
- first / last observed time
- latest PIR completion time
- evidence SHA-256

Account scorecards only count recurrence clusters that contain that account.

## Reliability Score

The score is deterministic and bounded to 0–100.

Weights:

    ACK SLO          25 points
    Recovery SLO     35 points
    Ambiguity        20 points
    Circuit Opens    10 points
    Recurrence       10 points

ACK and Recovery points scale against their configured SLO target.

Ambiguity receives full points while the rate is at or below the configured
ambiguity target and degrades above that threshold.

Circuit and Recurrence points are reduced by repeated events.

Grades:

    A >= 95
    B >= 85
    C >= 75
    D >= 60
    F < 60

The score is an operational reliability indicator only. It cannot authorize or
block a provider write by itself.

## Database

Migration:

    053_shrimp_bilibili_reliability_scorecard.sql

Tables:

    shrimp_bilibili_recurrence_clusters
    shrimp_bilibili_reliability_scorecards

Scorecard rows are immutable.

Recurrence clusters are recomputed deterministically from completed PIR data.

## Control Center

Workspace:

    /animation/reliability

The page shows:

- current Global Reliability Score / Grade
- Avg ACK
- P95 MTTR
- Provider Ambiguity rate
- Circuit open count
- recurring Root Cause count
- ACK / Recovery Error Budget
- Global / Account scorecard history
- Root Cause recurrence clusters

The page is read-only and requires no Gate key.

## APIs

Read-only:

- GET /v1/shrimp-animation/bilibili-reliability-dashboard
- GET /v1/shrimp-animation/bilibili-reliability-scorecards
- GET /v1/shrimp-animation/bilibili-recurrence-clusters

Protected generation:

- POST /internal/shrimp-animation/bilibili-reliability-scorecard

The generation endpoint uses the existing Bilibili Health Monitor Key or
CRON_SECRET authorization model.

## Schedule

Workflow:

    .github/workflows/bilibili-reliability-scorecard.yml

Schedule:

    37 0 * * *

The workflow generates one daily rolling scorecard run.

Required repository secrets:

- SHRIMP_BILIBILI_HEALTH_MONITOR_URL
- SHRIMP_BILIBILI_HEALTH_MONITOR_KEY

If either is absent, the workflow safely skips.

## Safety invariants

- Scorecard generation performs no provider writes.
- Recurrence Detection performs no provider writes.
- Reliability Grade cannot close or open a Circuit.
- Error Budget exhaustion does not bypass Step 9/10/10B Gates.
- Root Cause clustering cannot change Incident Recovery Approval.
- Recovery still follows Step 10B.10.
- Incident ownership/SLA/PIR still follows Step 10B.11.
