# Shrimp Animation Provider v0.1 — Step 10B.23

## Multi-Signer Threshold + Root Transition Dual-Control + OpenBao Live Transit Acceptance + Key Compromise Recovery Drill

### GitHub-first references

Step 10B.23 intentionally reuses established open-source trust patterns:

- `theupdateframework/python-tuf`
  - top-level Root signing thresholds
  - old-root + new-root continuity during key rotation
  - versioned root trust
- `secure-systems-lab/securesystemslib`
  - Ed25519 key verification
  - multi-signature threshold semantics
  - DSSE threshold verification model
- `in-toto/in-toto`
  - append signatures until threshold is satisfied
  - unique-key threshold counting
- `openbao/openbao`
  - Ed25519 Transit signing
  - Transit verification
  - versioned key rotation
  - historical signature verification

No bespoke cryptographic primitive is introduced.

## Root transition model

A candidate Root cannot be applied until both independent controls pass:

1. Cryptographic continuity
   - previous Root signature threshold is met
   - candidate Root signature threshold is met
   - unique key fingerprints are counted only once
2. Human dual-control
   - Approver A approves
   - Approver B approves
   - A and B use independent secrets
   - neither approval secret may equal the signing rotation secret

Only after both controls pass can the signing rotation gate APPLY the candidate Root.

All plans, signatures, approvals and applications are append-only immutable audit records.

## OpenBao live acceptance

GitHub Actions starts the official `openbao/openbao` container in isolated dev mode.

The acceptance performs real Transit operations:

```
enable transit
→ create Ed25519 key v1
→ sign v1
→ verify v1
→ rotate exactly once
→ verify historical v1 signature after rotation
→ sign v2
→ verify v2
→ persist immutable acceptance result
```

Expected write budget:

- sign writes: exactly 2 for the acceptance scenario
- rotate writes: exactly 1
- Production deployment writes: 0
- Bilibili provider writes: 0

## Compromise recovery drill

A recovery drill passes only when:

- compromised key is absent from the current TUF Root
- TUF Root chain verifies
- every declared affected historical Proof Bundle fails verification
- every affected bundle reports `KEY_REVOKED_AT_SIGNING_TIME`

This proves the distinction between:

- scheduled retirement/revocation after signing → historical proof remains valid
- forensic compromise effective before signing → affected proof becomes invalid

## Migration

Migration 065 adds immutable audit tables for:

- Root transition plans
- transition signatures
- dual-control approvals
- transition applications
- OpenBao live acceptances
- compromise recovery drills

Migrations 001–065 remain immutable and are not rewritten. Migration 066 only extends the already-deployed trust-root transition constraint to admit `COMPROMISE_RECOVERY`.

## Acceptance target

```
Migration 001 → 066
→ python-tuf Root continuity
→ previous threshold PASS
→ candidate threshold PASS
→ duplicate signer cannot count twice
→ Approver A only: APPLY blocked
→ Approver A + B: dual-control PASS
→ Root APPLY
→ trust chain PASS
→ retroactive compromise
→ affected bundle FAIL
→ recovery drill PASS
→ real OpenBao v1 sign/verify
→ one live rotate
→ historical v1 verify after rotation
→ v2 sign/verify
→ full pytest
→ production-disabled safety check
```
