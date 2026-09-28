# v0.3 Human Release Gate

## Purpose

This phase adds release-review infrastructure only. It does **not** deploy, publish,
push artifacts, modify production systems, or bypass Controlled Live LLM Acceptance.

Controlled Live LLM Acceptance is currently deferred. Therefore release candidates
created from MOCK/OpenHands acceptance runs remain:

```text
WAITING_LIVE_VALIDATION
live_validation_verified=false
deployment_enabled=false
```

## State machine

```text
ARTIFACT_READY
  -> WAITING_LIVE_VALIDATION
  -> READY_FOR_REVIEW          (requires real PROXY live verification)
  -> RELEASE_APPROVED          (requires separate human release decision)
     or RELEASE_REJECTED

RELEASE_APPROVED does not enable deployment.
```

## Hard invariants

- `live_validation_required=true`
- `deployment_enabled=false` enforced by database constraint and trigger
- `READY_FOR_REVIEW` requires:
  - approved current proposal
  - `execution_enabled=false`
  - OpenHands gateway mode `PROXY`
  - `budget_status=WITHIN_BUDGET`
  - `live_model_verified=true`
  - OpenHands exit code 0
  - sandbox request `ARTIFACT_READY`
  - at least one captured artifact
  - at least one passed independent test
- `RELEASE_APPROVED` is impossible without the same live-validation evidence.
- terminal release decisions are immutable.
- approval uses a separate `HUMAN_RELEASE_KEY`; it does not reuse the build approval key.
- no deployment action or production credential is introduced by this phase.

## Audit data

Each release candidate stores:

- request/run/proposal/revision/source fingerprint binding
- deterministic artifact manifest and SHA-256
- test summary
- live-validation status
- release status
- deployment disabled invariant

Release decisions are append-only audit records containing decision, reason, actor,
candidate status, and timestamp.

## APIs

Read/audit:

- `GET /v1/release-candidates`
- `GET /v1/release-candidates/{candidate_id}`
- `GET /v1/release-candidates/{candidate_id}/decisions`

Candidate creation:

- `POST /v1/release-candidates/from-request/{request_id}`

Human decision:

- `POST /v1/release-candidates/{candidate_id}/decision`
- header: `X-Release-Key`

## Deferred validation

The real-model Controlled Live LLM Acceptance remains deferred by explicit human choice.
Until it succeeds, the gate is intentionally fail-closed at
`WAITING_LIVE_VALIDATION`.
