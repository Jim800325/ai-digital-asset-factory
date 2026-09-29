# Controlled Release Gate Fail-Closed Persistence Acceptance

Status: PASSED
Date: 2026-09-29
Environment: Vercel Production + Neon pooled PostgreSQL

## Fixture

- Release Candidate ID: `00000000-0000-0000-0000-000000001709`
- Source fingerprint: `test-only-release-gate-fixture-v1`
- Fixture purpose: verify that a Release Candidate with no matching VERIFIED Live Acceptance Audit cannot be approved.
- No model call was performed by the fixture.
- Deployment remained disabled.

## Preconditions

- Database: AVAILABLE
- Database target: NEON_POOLER
- sslmode: require
- Migrations through 017 applied
- HUMAN_RELEASE_KEY configured
- Integrity manifest: VERIFIED
- Manifest root valid: true
- Chain valid: true

## Expected gate failure

The fixture Review Package used:

`source_tree_sha256 = ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff`

This source tree intentionally has no matching VERIFIED Live Acceptance Audit.

Expected blocking reason:

`matching_live_acceptance_audit_not_found`

## Observed result

A TEST_ONLY backend APPROVE attempt was made through the normal Release Decision API using the normal X-Release-Key requirement.

Observed persisted block event:

- Block event ID: `73c3fad2-c643-4f9c-a7bd-5ee38d27b06b`
- attempted_decision: `APPROVE`
- actor: `controlled-release-gate-test-ui`
- integrity_status: `ORPHANED`
- blocking_reasons:
  - `matching_live_acceptance_audit_not_found`
- manifest_root_sha256:
  `83d24e3c22ecd436709776dad6565fe637d594b9c309a3cd3beeae67650e8c8c`
- chain_head_sha256:
  `3339917f9d111c9cb729aa6030efb977d0428c17d720cdc3cbfbe25619668880`

## Safety assertions

All required assertions passed:

- UI normal Approve remained locked.
- Backend APPROVE was rejected.
- `release_gate_blocks` persisted the rejection.
- `release_decisions` remained empty for the fixture.
- Candidate status remained `READY_FOR_REVIEW`.
- `deployment_enabled` remained `false`.
- Integrity manifest remained VERIFIED.
- No release or deployment occurred.
- No model call occurred for this test fixture.

## Cleanup

Migration 018 removes only this fixed TEST_ONLY fixture and its related persistence records from the Production database after this acceptance record has been committed to the repository.

This document is the durable, read-only acceptance evidence after database fixture cleanup.
