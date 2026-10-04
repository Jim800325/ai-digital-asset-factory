# Step 10B.20 — Certification Trust Chain Verification + Attestation Integrity Audit + Renewal SLA + Missed-Renewal Escalation

## Purpose

Step 10B.19 made Certifications finite and linked them through immutable
Attestations.

Step 10B.20 verifies that the chain itself is still trustworthy.

The new lifecycle is:

    Certification / Attestation History
      -> Trust Chain Verification
      -> Integrity Audit
      -> Renewal SLA Evaluation
      -> Audit Proof Snapshot
      -> Governance Evidence

The verifier never repairs evidence automatically.

It detects, records, notifies, and escalates to human Governance.

## Trust Chain verification

The verifier reads all Certifications in certified_at order and all
Reliability Attestations in created_at order.

For each Certification it recomputes:

    SHA-256(canonical certification_snapshot)

and compares it with:

    certification_sha256

It also recomputes:

    SHA-256(canonical stability_baseline)

and compares it with:

    baseline_sha256

Certification-chain checks include:

- first Certification must not point to a previous Certification
- every later Certification.previous_certification_id must point to the
  immediately preceding Certification in the historical chain
- attestation_sequence must increase across Certification generations
- only one non-SUPERSEDED Certification may be current

Issue codes include:

- CERTIFICATION_SHA_MISMATCH
- BASELINE_SHA_MISMATCH
- FIRST_CERTIFICATION_HAS_PREVIOUS
- CERTIFICATION_CHAIN_POINTER_MISMATCH
- CERTIFICATION_SEQUENCE_NOT_INCREASING
- MULTIPLE_CURRENT_CERTIFICATIONS

## Attestation integrity verification

For every Attestation the verifier recomputes:

    evidence_sha256 =
      SHA-256(canonical evidence_snapshot)

Then it reconstructs the Attestation identity material:

    certification_id
    previous_attestation_id
    attestation_type
    attestation_sequence
    attestation_status
    evidence_sha256

and recomputes:

    attestation_sha256

Attestation-chain checks include:

- first Attestation must not point to a previous Attestation
- each later previous_attestation_id must point to the immediately preceding
  Attestation in the chain
- created_at must not move backwards
- referenced Certification must exist

Issue codes include:

- ATTESTATION_EVIDENCE_SHA_MISMATCH
- ATTESTATION_SHA_MISMATCH
- FIRST_ATTESTATION_HAS_PREVIOUS
- ATTESTATION_CHAIN_POINTER_MISMATCH
- ATTESTATION_TIME_ORDER_INVALID
- ATTESTATION_CERTIFICATION_MISSING

## Integrity Audit

Every audit freezes:

- audit status
- Certification count
- Attestation count
- current Certification count
- issue codes
- complete verification snapshot
- snapshot SHA
- Audit SHA
- evaluator
- evaluated timestamp

Possible status:

    PASS
    FAIL

Audit records are immutable.

Identical evidence produces the same deterministic Audit SHA and is
idempotent.

## Integrity failure

If any trust-chain issue exists:

    audit_status = FAIL

The system queues:

    RELIABILITY_CERTIFICATION_INTEGRITY_FAILURE

with severity:

    CRITICAL

The next Governance Review becomes:

    FREEZE_RECOMMENDED

with reason:

    certification trust chain integrity audit failed

This is a recommendation only.

Integrity failure does not automatically:

- freeze Policy Control
- change quota
- change Circuit
- release or change Claims
- retry a provider write
- call Bilibili

Any actual freeze still uses the existing Step 10B.14 / 10B.15 human
Governance and Apply gates.

## Renewal SLA

Configuration:

    SHRIMP_BILIBILI_RENEWAL_SLA_HOURS=72
    SHRIMP_BILIBILI_MISSED_RENEWAL_CRITICAL_HOURS=24

The normal Re-Certification warning begins at:

    renewal_due_at

The SLA due time is:

    renewal_due_at + renewal_sla_hours

With the default 30-day Certification / 7-day warning policy:

    renewal_due_at = day 23
    SLA deadline = day 26

The system therefore has time to escalate before hard Certification expiry.

## Renewal SLA escalation

If the current Certification remains non-SUPERSEDED after the SLA deadline:

    RENEWAL_SLA_BREACH
    severity = WARNING

If it remains current at or after expires_at:

    MISSED_RENEWAL
    severity = WARNING

If it remains current after:

    expires_at + missed_renewal_critical_hours

the system creates:

    MISSED_RENEWAL_CRITICAL
    severity = CRITICAL

Escalation evidence freezes:

- Certification ID / key / status
- Certification SHA
- Baseline SHA
- renewal_due_at
- expires_at
- escalation due_at
- first breached_at
- first overdue_minutes
- Evidence SHA
- Escalation SHA

Escalation evidence is immutable.

The Control Center additionally computes current_overdue_minutes dynamically,
without rewriting the immutable breach evidence.

## Escalation lifecycle

Only one OPEN escalation of a given type may exist for a Certification.

When a Certification becomes SUPERSEDED, its OPEN renewal escalations are
marked:

    SUPERSEDED

No historical evidence fields are changed.

Notifications are emitted only when an escalation is first opened, preventing
daily duplicate alert spam.

Notification type:

    RELIABILITY_CERTIFICATION_RENEWAL_SLA

Severity follows the escalation severity.

## Governance semantics

Renewal SLA and missed-renewal escalations are operational governance signals.

Any OPEN renewal escalation makes the next Governance Review at least:

    CAUTION

with reason:

    reliability certification renewal SLA breached;
    human governance follow-up required

A renewal SLA breach by itself does not generate FREEZE_RECOMMENDED.

Stronger independent evidence still wins, for example:

- Trust Integrity FAIL
- Error Budget Fast Burn
- Critical Regression
- repeated critical Root Cause
- Post-Unfreeze Refreeze Recommendation

Those conditions can still generate:

    FREEZE_RECOMMENDED

## Audit Proof

Step 10B.20 creates a deterministic JSON Audit Proof.

The Proof includes:

- latest Integrity Audit ID
- Integrity Audit status
- Audit SHA
- Audit Snapshot SHA
- current Certification identity
- current Certification SHA
- current Baseline SHA
- validity window
- Attestation Sequence
- complete ordered Attestation chain identities
- Attestation Evidence SHA values
- Attestation SHA values
- Renewal SLA Escalation identities
- Escalation Evidence SHA values
- Escalation SHA values
- automatic_policy_change = false
- provider_writes = false

The complete Proof Snapshot receives:

    proof_snapshot_sha256

The immutable proof identity receives:

    proof_sha256

Audit Proof records are immutable.

## Proof export

Read-only endpoint:

    GET /v1/shrimp-animation/bilibili-certification-audit-proof/latest

returns the latest deterministic JSON Proof.

The Control Center includes:

    Open latest JSON Proof

which opens this endpoint directly.

No approval key or provider credential is required because the Proof contains
only non-secret audit identities and evidence hashes.

## Scheduled cycle

The daily Reliability workflow becomes:

1. Generate Reliability Scorecard
2. Trend / Burn / Regression analysis
3. Post-Restore Certification Cycle
4. Certification Expiry / Renewal Cycle
5. Certification Trust Audit / Renewal SLA / Proof
6. Governance Review

Protected endpoint:

    POST /internal/shrimp-animation/bilibili-certification-trust-audit-cycle

Authorization:

- SHRIMP_BILIBILI_HEALTH_MONITOR_KEY
- or CRON_SECRET

The scheduled cycle can:

- verify SHA chains
- write immutable Integrity Audit
- evaluate Renewal SLA
- open Escalations
- queue notifications
- generate deterministic Proof

It cannot:

- freeze Policy Control
- approve Re-Certification
- change quota
- change Circuit
- mutate Certification evidence
- mutate Attestation evidence
- call Bilibili

## Database

Migration:

    061_shrimp_bilibili_certification_trust_audit.sql

New tables:

- shrimp_bilibili_certification_integrity_audits
- shrimp_bilibili_renewal_sla_escalations
- shrimp_bilibili_certification_audit_proofs

All Integrity Audit and Audit Proof rows are immutable.

Renewal Escalation evidence is immutable; only lifecycle status may move from
OPEN to CLOSED/SUPERSEDED.

## APIs

Read:

- GET /v1/shrimp-animation/bilibili-certification-trust-audit
- GET /v1/shrimp-animation/bilibili-certification-integrity-audits
- GET /v1/shrimp-animation/bilibili-renewal-sla-escalations
- GET /v1/shrimp-animation/bilibili-certification-audit-proofs
- GET /v1/shrimp-animation/bilibili-certification-audit-proof/latest

Protected scheduled execution:

- POST /internal/shrimp-animation/bilibili-certification-trust-audit-cycle

## Control Center

Workspace:

    /animation/reliability-review

now shows:

- Trust Chain VALID / FAIL
- latest Integrity Audit
- exact issue codes
- Certification count
- Attestation count
- Audit SHA
- Renewal SLA duration
- Critical missed-renewal threshold
- OPEN escalation count
- CRITICAL escalation count
- current overdue minutes
- Escalation Evidence SHA
- latest Audit Proof SHA
- Proof Snapshot SHA
- Audit Proof history
- read-only latest JSON Proof link

## Safety invariants

- Certification history is verified, never silently trusted.
- SHA mismatch is recorded, never automatically repaired.
- Broken trust chain cannot silently remain NORMAL governance evidence.
- Integrity FAIL recommends freeze but does not execute it.
- Renewal SLA breach escalates to human Governance but does not auto-freeze.
- Escalation notifications are deduplicated.
- Immutable evidence remains immutable.
- Proof export contains hashes and audit metadata, not credentials.
- Trust Audit / SLA / Proof perform zero Bilibili provider writes.
