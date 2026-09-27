# Build Proposal & Human Approval Gate v0.3

v0.3 introduces a controlled transition from research readiness to a human-reviewed build proposal.

The system remains non-executing. APPROVED does not mean executable.

## State machine

BUILD_READY
→ BUILD_PROPOSAL
→ PENDING_APPROVAL
→ APPROVED or REJECTED

Any loss of current BUILD_READY qualification, or a change in proposal source state, invalidates the proposal and moves it to STALE.

A changed research basis produces a new proposal revision that returns to PENDING_APPROVAL.

## Proposal contents

Each build proposal contains:

- Objective
- Artifact type
- Scope derived from the current Research Report
- Success criteria
- Constraints
- Proposed technical stack
- Sandbox policy
- Source snapshot
- Source fingerprint
- Revision number

The source fingerprint covers the current opportunity state, validation state, and a hash of the Research Report fields used to construct the proposal.

## Human Approval Gate

Proposal decisions are allowed only when all of the following are true:

1. Proposal status is PENDING_APPROVAL.
2. Opportunity remains BUILD_READY.
3. Research Report remains GENERATED.
4. Research Validation remains CURRENT and passed.
5. Proposal source fingerprint still matches current research state.
6. The approval API receives the configured HUMAN_APPROVAL_KEY.

Every APPROVE or REJECT action creates an append-only build_proposal_decisions record with proposal revision, reason, actor, and timestamp.

## Approval does not execute

In v0.3:

- requires_human_approval is always true.
- execution_enabled is always false.
- the database enforces execution_enabled=false.
- APPROVED proposals cannot deploy, publish, pay, send outreach, modify production, or invoke OpenHands.
- build execution remains disabled in the health endpoint.

A later migration is required before any sandbox executor can be enabled.

## Sandbox policy encoded in every proposal

- network: DENY_BY_DEFAULT
- filesystem: WORKSPACE_ONLY
- production_credentials: DENY
- deployment: DENY
- external_side_effects: DENY
- human_release_required: true

## API

Read:

- GET /v1/build-proposals
- GET /v1/build-proposals/{proposal_id}
- GET /v1/build-proposals/{proposal_id}/decisions

Human decision:

- POST /v1/build-proposals/{proposal_id}/decision
- Header: X-Approval-Key
- Body fields: decision, reason, actor

HUMAN_APPROVAL_KEY must be configured outside source control.

## Next boundary

The next phase may connect APPROVED proposals to an isolated sandbox executor, but only after a new execution-gate migration and acceptance test. Production deployment and release must remain separate human-gated actions.
