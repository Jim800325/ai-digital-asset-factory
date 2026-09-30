# Controlled Production Release Executor v0.1 — Design

Status: **DESIGN ONLY**

This phase starts after the completed Deployment Authorization Preview Acceptance.

Dependency:

- `feature/deployment-authorization-v0.1`
- terminal Deployment Plan status `AUTHORIZED_FOR_DEPLOYMENT`
- persisted human `AUTHORIZE` decision
- Release / Review / Live Acceptance provenance remains verifiable

This document intentionally adds **no Production execution path**. It freezes the
contract that a later implementation must satisfy.

## 1. Goal

Turn a previously authorized immutable Deployment Plan into a controlled,
auditable Vercel Production release without allowing authorization itself to
cause deployment.

The executor must separate four concepts:

1. Release Approval
2. Deployment Authorization
3. Production candidate preparation
4. Production traffic promotion

No earlier stage may imply or automatically trigger a later stage.

## 2. Hard boundaries

The initial implementation MUST default to:

```text
production_execution_enabled = false
automatic_execution = false
automatic_promotion = false
deployment_executor = DISABLED
```

It MUST NOT:

- promote merely because a Deployment Plan is `AUTHORIZED_FOR_DEPLOYMENT`
- accept arbitrary project/team IDs at execution time
- deploy current Git HEAD instead of the immutable release snapshot
- read a mutable branch as the source of release bytes
- reuse `HUMAN_RELEASE_KEY` or `HUMAN_DEPLOYMENT_KEY` as the execution credential
- replay an ambiguous Vercel write automatically
- modify Production when the database or provenance registry is unavailable
- use a Preview-built deployment as Production merely for convenience
- expose Vercel tokens or Production environment values in logs or audit records

## 3. Vercel release model

The v0.1 Vercel path is deliberately two-step.

### Step A — prepare a Production-configured candidate with no Production traffic

The execution adapter should create a deployment using Production configuration
and Production environment values while suppressing Production domain assignment.

The documented Vercel CLI primitive is:

```text
vercel deploy --prod --skip-domain
```

This is preferred over promoting an ordinary Preview deployment because promotion
does not rebuild the deployment. A Preview deployment could therefore contain
Preview build/runtime configuration rather than the intended Production
configuration.

The prepared deployment must reach `READY` and pass candidate health checks
before it can become promotable.

### Step B — explicit promotion

Only after a separate human execution decision may Production traffic change.

The Vercel primitive is:

```text
POST /v10/projects/{projectId}/promote/{deploymentId}
```

Vercel documents that this operation points Production traffic at the selected
deployment and does not rebuild it.

The executor must capture the current Production deployment ID immediately before
promotion so a bounded rollback target is known.

## 4. Execution adapter boundary

Provider-specific effects live behind an adapter interface. Core release logic
must not invoke shell commands or Vercel APIs directly.

Conceptual interface:

```python
class ProductionExecutionAdapter:
    prepare_candidate(execution_snapshot) -> CandidateDeployment
    read_deployment(deployment_id) -> DeploymentState
    read_current_production(project_id) -> ProductionPointer
    promote(project_id, deployment_id) -> PromoteReceipt
    rollback(project_id, previous_deployment_id) -> RollbackReceipt
```

Initial adapter:

```text
VERCEL_CONTROLLED_EXECUTOR
```

The control-plane HTTP application MUST NOT run a long-lived Vercel CLI process
inside a request handler. A later implementation should use a dedicated release
runner/worker. The API persists commands and state; the runner performs bounded
provider actions.

## 5. Immutable execution bundle

Execution MUST be driven from the immutable Release Review snapshot, never from
the repository branch at execution time.

Before preparation, the executor materializes an execution bundle containing:

- exact artifact paths
- exact artifact bytes
- SHA-256 per artifact
- content length per artifact
- Review Package ID and SHA-256
- review source-tree SHA-256
- Deployment Plan ID and SHA-256
- Deployment Authorization Decision ID
- Live Acceptance Audit ID
- audit evidence SHA-256
- audit chain SHA-256
- source commit and deployment source commit
- target Vercel project/team IDs
- executor schema version

The canonical bundle receives:

```text
execution_bundle_sha256
```

Fail closed when:

- an artifact listed in the immutable manifest has no persisted bytes
- any artifact hash or length differs
- a path is absolute, escapes the workspace, or is duplicated
- the Review Package / Plan / authorization binding differs
- the target project/team is not the configured allowlisted Production target

## 6. Third human gate

A new independent secret is required:

```text
HUMAN_PRODUCTION_EXECUTION_KEY
```

It must be independent of:

- `HUMAN_RELEASE_KEY`
- `HUMAN_DEPLOYMENT_KEY`

Production promotion requires an explicit persisted human execution decision
bound to:

- execution ID
- execution SHA-256
- Deployment Plan ID / SHA-256
- prepared Vercel deployment ID
- prepared deployment URL
- captured target project/team
- candidate verification result

The HTTP request must not accept a different Vercel deployment ID than the one
already persisted by the prepare stage.

## 7. Proposed persistence model

Migration `023_controlled_production_release_executor.sql` should add three
append-safe structures.

### production_release_executions

One row per Deployment Plan.

Important fields:

- `id`
- `deployment_plan_id UNIQUE`
- `deployment_authorization_decision_id`
- `execution_status`
- `execution_sha256 UNIQUE`
- `execution_bundle_sha256`
- `target_project_id`
- `target_team_id`
- `candidate_vercel_deployment_id`
- `candidate_vercel_url`
- `previous_production_deployment_id`
- `production_vercel_deployment_id`
- `production_execution_enabled boolean CHECK (...)`
- timestamps for prepared / promoted / verified / rolled back

### production_release_execution_decisions

Append-only human decision:

- `PROMOTE`
- `ABORT`

Exactly one terminal promotion decision per execution.

### production_release_execution_events

Append-only operational evidence:

- PREPARE_REQUESTED
- CANDIDATE_CREATED
- CANDIDATE_READY
- CANDIDATE_VERIFIED
- PROMOTE_REQUESTED
- PROMOTE_PROVIDER_ACCEPTED
- PRODUCTION_POINTER_VERIFIED
- PRODUCTION_HEALTH_VERIFIED
- RECONCILIATION_REQUIRED
- ROLLBACK_REQUESTED
- ROLLBACK_PROVIDER_ACCEPTED
- ROLLBACK_VERIFIED
- FAILURE

Provider response bodies must be redacted before persistence.

## 8. State machine

Allowed high-level states:

```text
AUTHORIZED_FOR_DEPLOYMENT
        |
        v
PREPARING
        |
        v
READY_FOR_PROMOTION
        |
        +---- ABORTED
        |
        v
PROMOTION_REQUESTED
        |
        +---- PROMOTION_UNKNOWN
        |
        v
PRODUCTION_ACTIVE
        |
        +---- ROLLBACK_REQUIRED
                  |
                  v
              ROLLED_BACK
```

Preparation failures terminate as `PREPARE_FAILED`.

A write whose provider outcome is ambiguous MUST become
`PROMOTION_UNKNOWN` / `ROLLBACK_UNKNOWN`. The executor must reconcile by
reading Vercel state before any further write. It must never blindly replay the
provider mutation.

## 9. Pre-execution revalidation

Immediately before candidate preparation and again before promotion:

- Deployment Plan exists and is `AUTHORIZED_FOR_DEPLOYMENT`
- exactly one persisted `AUTHORIZE` decision exists
- plan SHA-256 matches the stored immutable material
- Release Candidate remains `RELEASE_APPROVED`
- Release Candidate is not archived
- `deployment_enabled=false`
- Review Package remains GENERATED and complete
- exact Review Package / tree hashes remain unchanged
- Live Acceptance registry is VERIFIED
- the matched audit entry remains unchanged
- deployment source mapping remains unchanged
- target project/team exactly match executor allowlist

### Append-only registry evolution

A later valid audit entry must not silently destroy an already authorized release.

Execution revalidation therefore distinguishes:

- **mutation/removal of the bound audit or its evidence** -> BLOCK
- **verified append-only extension after the stored chain hash** -> ALLOW

The implementation should verify that the stored audit chain hash is still a
valid prefix/ancestor in the current verified chain. Merely comparing the current
global chain head for exact equality is insufficient for a long-lived execution
authorization.

## 10. Candidate verification

Before promotion, the prepared no-traffic deployment must satisfy:

- provider state `READY`
- expected Vercel project/team
- expected execution metadata / execution ID when provider metadata is available
- configured health endpoint returns accepted status
- no fatal runtime errors in a bounded observation window
- artifact/source provenance remains the expected bundle
- Production pointer has not changed unexpectedly since the execution snapshot

A Production pointer change by another actor is a conflict and blocks promotion.

## 11. Promotion transaction model

Promotion is not treated as a database transaction.

Sequence:

1. lock execution row
2. revalidate all provenance
3. read and persist current Production deployment as rollback target
4. persist PROMOTE human decision
5. persist `PROMOTION_REQUESTED`
6. commit DB transaction
7. call Vercel promote exactly once
8. read Vercel current Production pointer
9. if candidate is active, persist provider acceptance
10. run Production health checks
11. persist `PRODUCTION_ACTIVE` only after pointer + health verification

If step 7 times out, do not replay. Read current Production state and reconcile.

## 12. Rollback boundary

Rollback may target **only** the captured previous Production deployment ID.

No arbitrary rollback target may be supplied by an API client.

v0.1 should support two policies:

- `MANUAL_ROLLBACK` — operator explicitly authorizes rollback
- `BOUNDED_AUTO_ROLLBACK` — optional, separately configured, only when
  post-promotion health fails and only to the captured previous deployment

Default:

```text
rollback_policy = MANUAL_ROLLBACK
```

Automatic rollback must remain disabled until its own Preview acceptance exists.

## 13. API surface

Design target:

```text
POST /v1/deployment-plans/{plan_id}/execution
GET  /v1/production-release-executions/{execution_id}
POST /v1/production-release-executions/{execution_id}/prepare
POST /v1/production-release-executions/{execution_id}/decision
POST /v1/production-release-executions/{execution_id}/reconcile
POST /v1/production-release-executions/{execution_id}/rollback
```

All writes require the independent execution key.

No endpoint accepts:

- arbitrary source commit
- arbitrary artifact bytes
- arbitrary Vercel deployment ID for promotion
- arbitrary rollback deployment ID
- Production alias/domain reassignment instructions

## 14. Feature flags

Required configuration:

```text
CONTROLLED_PRODUCTION_EXECUTOR_ENABLED=false
PRODUCTION_PROMOTION_ENABLED=false
PRODUCTION_ROLLBACK_ENABLED=false
```

Implementation acceptance begins with all three false.

Preview executor code must refuse the real Production project ID. Live Preview
acceptance uses a dedicated sacrificial Vercel project.

## 15. Observability and audit

Every state transition records:

- actor
- execution ID
- execution SHA-256
- plan ID / SHA-256
- provider deployment ID when known
- event type
- previous state / next state
- timestamp
- redacted provider result hash

Never persist:

- Vercel access token
- Production environment values
- human execution secret
- raw Authorization headers

## 16. Failure policy

Fail closed on:

- database outage
- registry unavailable / invalid
- Vercel read failure during required revalidation
- mismatched project/team
- missing artifact bytes
- hash mismatch
- unexpected candidate archival
- changed Production pointer
- ambiguous provider mutation

Reads may retry with bounded backoff.

Provider writes are never blindly retried.

## 17. Implementation sequence

Only after this design is accepted:

1. migration 023 + DB constraints
2. immutable execution snapshot / hashing
3. execution integrity gate
4. provider adapter interface with MOCK implementation
5. PREPARE state machine with provider writes disabled
6. third human gate
7. Vercel controlled adapter in Preview-only mode
8. sacrificial-project Preview acceptance
9. rollback acceptance
10. cleanup
11. separate Production enablement review

No step above enables the real Production executor by itself.

## 18. Exit criteria for the design phase

Design phase is complete when:

- state machine is frozen
- persistence model is frozen
- third human gate is accepted
- immutable artifact source is defined
- Vercel prepare/promote boundary is defined
- ambiguous-write reconciliation policy is defined
- rollback target/policy is defined
- Preview acceptance matrix is defined
- Production remains unchanged

At that point implementation may start with all provider mutations disabled.
