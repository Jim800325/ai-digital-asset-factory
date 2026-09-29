# Release Gate Recovery Acceptance

Status: PASSED
Date: 2026-09-29
Environment: Vercel Production + Neon pooled PostgreSQL

## Recovery fixture

- Release Candidate ID: `00000000-0000-0000-0000-000000001911`
- Review Package ID: `00000000-0000-0000-0000-000000001912`
- Source fingerprint: `test-only-release-gate-recovery-v1`
- VERIFIED Live Acceptance Audit: `8b385170ee00c667`
- No model call was performed by this recovery fixture.
- Deployment remained disabled.

## Provenance compatibility correction

During recovery acceptance, the system identified that Live Acceptance and Release Review originally used different source-tree canonicalization:

- Live Acceptance: relative_path + sha256 + byte_size
- Release Review: relative_path + sha256 + byte_size + media_type

The Release Integrity Gate now derives an explicit
`acceptance_provenance_tree_sha256` from the Review Package artifact
manifest using the Live Acceptance canonicalization.

For this fixture:

`acceptance_provenance_tree_sha256 =
f34dc11399590141886aacc006ee9c8c647bf517370684d516b0987f4aa7122c`

This exactly matched the VERIFIED Audit source tree.

## Verified audit binding

- audit_id: `8b385170ee00c667`
- audit_acceptance_status: `PASSED`
- audit_integrity_status: `VERIFIED`
- source_commit:
  `5e09b40649b07337aa895efca2cc37cfd146b459`
- deployment_source_commit:
  `5e09b40649b07337aa895efca2cc37cfd146b459`
- Vercel deployment:
  `dpl_B5j5bC2ng82AwVboDrhL8VBqpnj6`
- audit evidence SHA-256:
  `34329712ed6be40c78eaa24076c8be17dd703dcbe62f1dff77945a61eb997fc0`
- audit chain SHA-256:
  `3339917f9d111c9cb729aa6030efb977d0428c17d720cdc3cbfbe25619668880`
- manifest root SHA-256:
  `83d24e3c22ecd436709776dad6565fe637d594b9c309a3cd3beeae67650e8c8c`

## Successful decision

- Decision ID: `6659c1fa-f1ad-4826-b785-32030f8a421c`
- decision: `APPROVE`
- candidate_status: `RELEASE_APPROVED`
- actor: `human-review-ui`
- decided_at: `2026-09-29T07:05:53.082497Z`
- reason:
  `Controlled recovery acceptance with VERIFIED Live Acceptance provenance`
- review_package_sha256:
  `abababababababababababababababababababababababababababababababab`
- review source_tree_sha256:
  `8a6a84c1433fa6859768bfdb5ef9a57a4ec832c557bce0943e83bfaee8af46d6`

## Safety assertions

All required recovery assertions passed:

- Integrity status was VERIFIED.
- `can_approve` changed to true before the human decision.
- Human APPROVE persisted exactly one release decision.
- Candidate became `RELEASE_APPROVED`.
- Candidate became terminal: `can_approve=false`, `can_reject=false`.
- `deployment_enabled=false` after approval.
- Production deployment remained disabled.
- No model call occurred for the recovery fixture.
- The earlier ORPHANED fail-closed block record remained intact and separate.

## First attempt and correction

The first approval attempt was rejected by the database-level immutable review
package trigger because the historical Live Acceptance run had persisted hashes
but not artifact bytes. No decision was committed.

Migration 020 temporarily aligned the database rule for this single fixed
TEST_ONLY recovery fixture. After this acceptance passed, migration 021 removes
that TEST_ONLY exception and archives the fixture while retaining the durable
audit record in this repository.

This document is the durable, read-only evidence for the successful recovery
path of the Release Integrity Gate.
