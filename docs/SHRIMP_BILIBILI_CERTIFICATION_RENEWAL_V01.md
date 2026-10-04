# Step 10B.19 — Certification Expiry + Baseline Renewal + Reliability Attestation History + Governance Re-Certification

## Purpose

A Reliability Certification is not permanent.

Step 10B.19 gives every Certification a finite validity window, creates an
expiry warning before the deadline, generates a new baseline renewal candidate
from fresh reliability evidence, requires an independent human
Re-Certification Gate, and keeps every certification transition in an
immutable Reliability Attestation chain.

The lifecycle becomes:

    CERTIFIED
      -> EXPIRING
      -> Re-Certification Candidate
      -> Human APPROVE
      -> New CERTIFIED Certification
      -> Old Certification SUPERSEDED

If renewal is not completed before expiry:

    CERTIFIED / EXPIRING
      -> EXPIRED
      -> RECERTIFICATION_REQUIRED
      -> Human Governance Review at least CAUTION

Expiry never performs an automatic freeze and never writes to Bilibili.

## Default validity

Configuration:

    SHRIMP_BILIBILI_CERTIFICATION_VALID_DAYS=30
    SHRIMP_BILIBILI_CERTIFICATION_EXPIRY_WARNING_DAYS=7

A new Certification stores:

- valid_from
- renewal_due_at
- expires_at
- attestation_sequence
- previous_certification_id

With the defaults:

    valid_from = certification time
    renewal_due_at = certification time + 23 days
    expires_at = certification time + 30 days

The warning duration is clamped so it is always at least one day and always
shorter than the total validity duration.

## Certification states

Step 10B.19 extends the Certification state machine:

- CERTIFIED
- EXPIRING
- EXPIRED
- RECERTIFICATION_REQUIRED
- REOPEN_RECOMMENDED
- SUPERSEDED

Only one non-SUPERSEDED Certification may exist at a time.

This invariant is enforced with a partial unique index.

## EXPIRING

When:

    now >= renewal_due_at
    and
    now < expires_at

a CERTIFIED Certification becomes:

    EXPIRING

The system records an immutable:

    EXPIRY_WARNING

Reliability Attestation and queues:

    RELIABILITY_CERTIFICATION_EXPIRING

notification.

EXPIRING is still a current health proof until expires_at.

Long-term Reopen Evaluation continues while the Certification is EXPIRING.

The expiry warning does not:

- freeze policy
- change quota
- block Reservation
- change Circuit
- call Bilibili

## EXPIRED

When:

    now >= expires_at

a CERTIFIED or EXPIRING Certification becomes:

    EXPIRED

The system records:

    CERTIFICATION_EXPIRED

Attestation and queues:

    RELIABILITY_CERTIFICATION_EXPIRED

notification.

An EXPIRED Certification is no longer considered a current reliability proof.

Expiry does not automatically alter runtime Policy Control.

## Baseline Renewal Candidate

During EXPIRING / EXPIRED / RECERTIFICATION_REQUIRED the scheduled renewal
cycle attempts to create a Re-Certification Candidate.

Candidate generation requires:

- Policy Control = NORMAL
- quota multiplier = 100
- new Reservation allowed
- Global Reliability Scorecard exists
- Burn = HEALTHY
- zero open Regression
- zero recurring Root Cause
- zero unresolved Incident
- all Circuits CLOSED
- Reliability Score satisfies promoted minimum
- ACK satisfies current formal SLO
- Recovery satisfies current formal SLO
- Ambiguity satisfies current formal SLO
- current Scorecard SHA differs from the source Certification baseline

The last rule means Re-Certification must use a newer Reliability Scorecard.
The old Certification cannot be re-approved using the exact evidence that
created it.

## Frozen Candidate

A Candidate freezes:

- source Certification ID / key
- source Certification SHA
- source Baseline SHA
- source expiry time
- new Reliability evidence
- new Reliability evidence SHA
- proposed Stability Baseline
- proposed Baseline SHA
- promoted SLO
- Reopen Policy
- proposed valid_from
- proposed renewal_due_at
- proposed expires_at
- Candidate SHA

The validity window is frozen at Candidate generation time.

Approval uses those exact frozen timestamps. It does not recompute a new
validity period during the approval request.

## Evidence de-duplication

Only one PENDING Candidate may exist for a source Certification.

The combination:

    source_certification_id + new_evidence_sha256

is treated as the logical evidence identity.

If the same evidence has already been APPROVED, REJECTED, or made STALE, the
system refuses to generate another Candidate from that same evidence.

A new application requires newer reliability evidence.

## Independent Governance Re-Certification Gate

New secret:

    SHRIMP_BILIBILI_RECERTIFICATION_KEY

Header:

    X-Shrimp-Bilibili-Recertification-Key

The key must be independent from:

- Restore Acceptance Key
- Restore Approval Key
- Restore Apply Key
- Reliability Policy Apply Key
- Reliability Governance Key
- Health Monitor Key
- Incident Ops Key
- Recovery Approval Key
- Live Acceptance Key
- Step 9 Publish Authorization Key
- Step 10 Publisher Execution Key
- Human Review Key

The browser never persists this secret.

## Approval preflight

Human Re-Certification decision requires:

- Candidate status = PENDING_APPROVAL
- supplied Candidate SHA exactly matches
- current Reliability evidence SHA still matches Candidate evidence SHA
- Policy Control remains NORMAL / 100% / Reservation allowed

If current reliability evidence drifted:

    Candidate -> STALE

and a new Candidate must be generated.

## APPROVE

APPROVE performs one atomic database transaction:

1. lock Candidate
2. lock source Certification
3. persist immutable human Decision
4. mark source Certification SUPERSEDED
5. create a new Certification
6. link new Certification.previous_certification_id to source Certification
7. increment attestation_sequence
8. close/supersede any old open Certification Reopen Event
9. mark Candidate APPROVED
10. append source SUPERSEDED Attestation
11. append new RECERTIFICATION_APPROVED Attestation

If any insertion fails, the transaction rolls back and the previous
Certification remains current.

The new Certification receives a new:

- Certification key
- Certification SHA
- Baseline SHA
- validity window
- promoted SLO snapshot
- Reopen Policy snapshot

## REJECT

REJECT writes:

- immutable Re-Certification Decision
- Candidate -> REJECTED
- RECERTIFICATION_REJECTED Attestation

It does not change Policy Control and does not create a new Certification.

The same evidence cannot be submitted again.

## Reliability Attestation History

New table:

    shrimp_bilibili_reliability_attestations

Attestation types:

- INITIAL_CERTIFICATION
- EXPIRY_WARNING
- CERTIFICATION_EXPIRED
- RECERTIFICATION_APPROVED
- RECERTIFICATION_REJECTED
- REOPEN_RECOMMENDED
- SUPERSEDED

Attestation status:

- VALID
- WARNING
- EXPIRED
- APPROVED
- REJECTED
- REOPENED
- SUPERSEDED

Each Attestation freezes:

- Certification ID
- previous Attestation ID
- Attestation type
- Attestation sequence
- status
- evidence snapshot
- evidence SHA
- Attestation SHA
- actor
- timestamp

Attestations are immutable.

For legacy Step 10B.18 Certifications, the first Step 10B.19 renewal cycle
backfills the missing INITIAL_CERTIFICATION Attestation without rewriting the
Certification.

## Certification history chain

Step 10B.19 removes the old one-Certification-per-Observation/Acceptance
restriction.

Multiple Certifications may point to the same accepted Restore lifecycle.

They are linked through:

    previous_certification_id

and:

    previous_attestation_id

The database still permits only one current non-SUPERSEDED Certification.

A normal new Restore lifecycle also writes a SUPERSEDED Attestation for the
old Certification before writing the new INITIAL_CERTIFICATION Attestation.

## Governance integration

The Step 10B.14 Governance Evidence Snapshot now includes:

- current Certification ID / key
- Certification status
- Certification SHA
- Baseline SHA
- valid_from
- renewal_due_at
- expires_at
- Attestation Sequence
- whether a Re-Certification Candidate is pending

EXPIRING does not automatically cause CAUTION.

EXPIRED or RECERTIFICATION_REQUIRED makes the next Governance Review at least:

    CAUTION

with reason:

    reliability certification expired;
    governance re-certification required

Existing stronger reliability evidence still takes precedence and may produce:

    FREEZE_RECOMMENDED

Expiry itself never directly freezes runtime policy.

## Scheduled lifecycle

The daily Reliability workflow becomes:

1. Reliability Scorecard
2. Trend / Burn / Regression / Recommendations
3. Post-Restore Certification Cycle
4. Certification Expiry / Renewal Cycle
5. Governance Review

Protected endpoint:

    POST /internal/shrimp-animation/bilibili-certification-renewal-cycle

Authorization uses the existing Health Monitor Key / CRON_SECRET model.

The cycle can:

- mark EXPIRING
- mark EXPIRED
- generate a Renewal Candidate
- write Attestations
- queue expiry notifications

It cannot:

- approve a Re-Certification
- freeze Policy Control
- change quota
- change Circuit
- call Bilibili

## API

Read:

- GET /v1/shrimp-animation/bilibili-certification-renewal
- GET /v1/shrimp-animation/bilibili-recertification-candidates
- GET /v1/shrimp-animation/bilibili-recertification-decisions
- GET /v1/shrimp-animation/bilibili-reliability-attestations

Human decision:

- POST /v1/shrimp-animation/bilibili-recertification-candidates/{candidate_id}/decision

Header:

    X-Shrimp-Bilibili-Recertification-Key

Protected scheduled cycle:

- POST /internal/shrimp-animation/bilibili-certification-renewal-cycle

## Control Center

Workspace:

    /animation/reliability-review

now shows:

- current Certification validity
- valid_from
- renewal_due_at
- expires_at
- days until expiry
- whether Certification is still current
- whether Re-Certification is required
- Renewal Candidate history
- source Baseline SHA
- proposed Baseline SHA
- new evidence SHA
- APPROVE / REJECT actions
- Re-Certification Decision history
- complete Reliability Attestation History

The Re-Certification Key is used only for the current HTTP request and is not
stored in browser state.

## Migration

Migration:

    060_shrimp_bilibili_certification_renewal.sql

New tables:

- shrimp_bilibili_recertification_candidates
- shrimp_bilibili_recertification_decisions
- shrimp_bilibili_reliability_attestations

Certification table gains:

- valid_from
- renewal_due_at
- expires_at
- previous_certification_id
- attestation_sequence

The migration safely removes the old immutability trigger before historical
validity backfill and re-installs the Step 10B.19 state-aware immutable trigger
after the migration.

Historical Attestation Sequence is backfilled in Certification creation order.

## Safety invariants

- A Certification is never permanent.
- Expired Certification is not a current health proof.
- Expiry never automatically freezes runtime policy.
- Renewal requires a newer Reliability Scorecard.
- Renewal Candidate evidence is immutable.
- Candidate evidence drift makes approval fail closed.
- Human Re-Certification uses an independent key.
- Same decided evidence cannot be submitted again.
- Old Certification is never overwritten.
- New Certification gets a new SHA and new Baseline.
- Only one current Certification can exist.
- Attestation history is immutable.
- No Re-Certification operation performs a Bilibili provider write.
