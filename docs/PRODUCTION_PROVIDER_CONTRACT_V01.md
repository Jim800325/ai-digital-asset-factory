# Production Provider Contract v0.1

## Purpose

Production Provider Contract is the common execution contract between an approved product build and provider-specific digital-asset production.

It gives Micro-SaaS, dataset, intelligence-report, workflow, and content providers one state model for:

- provider registration;
- immutable contract snapshots;
- stage dependency graphs;
- manifest versioning;
- resumable retries;
- downstream invalidation;
- source-state reconciliation;
- resource and estimated-cost accounting;
- audit events.

The contract does not own release authorization, Production deployment, Production promotion/rollback, or external publishing.

## Entry gate

A provider job may be created only when:

1. the Build Proposal is `APPROVED`;
2. the proposal still matches the current Product BUILD_READY research state;
3. the provider asset class matches the opportunity;
4. `requires_human_build_approval=true`;
5. build proposal `execution_enabled=false` remains unchanged.

The provider job itself is database-guarded to:

```text
external_side_effects = DENY
production_execution_enabled = false
publish_enabled = false
```

## Provider specification

A provider registers a deterministic specification:

```python
ProductionProviderSpec(
    provider_key="example_provider",
    asset_class="CONTENT_IP",
    provider_version="v0.1",
    stages=(
        ProviderStageSpec("PLAN", "PLAN"),
        ProviderStageSpec("GENERATE", "GENERATE", ("PLAN",)),
        ProviderStageSpec("PACKAGE", "PACKAGE", ("GENERATE",)),
        ProviderStageSpec("QC", "QC", ("PACKAGE",)),
    ),
)
```

Dependencies must reference earlier stages. The v0.1 contract therefore accepts a deterministic DAG in topological order and rejects unknown/later dependencies.

Every job stores an immutable contract snapshot and does not silently adopt later provider-definition changes.

## Job state machine

```text
READY
  -> RUNNING
  -> READY
  -> ...
  -> ARTIFACT_READY
  -> QC_PASSED
```

Failure path:

```text
RUNNING
  -> WAITING_RETRY
  -> READY
  -> RUNNING
```

When the retry budget is exhausted:

```text
RUNNING -> FAILED
```

When upstream Product BUILD_READY or the approved proposal changes:

```text
ANY SAFE STATE -> STALE
```

`QC_PASSED` means provider execution and automated QC completed. It does not imply release approval or publishing permission.

## Manifest contract

Providers write structured manifests through `ProviderManifestEnvelope`.

Each manifest stores:

- job;
- stage;
- kind;
- schema version;
- monotonically increasing manifest version;
- canonical SHA-256;
- immutable JSON content;
- current/superseded state.

Manifest payload and identity fields are database-protected against mutation. A changed upstream manifest invalidates downstream stage outputs rather than editing history in place.

## Invalidation

The contract computes transitive downstream dependencies from the job's contract snapshot.

An upstream change:

```text
PLAN changed
  -> GENERATE STALE
  -> PACKAGE STALE
  -> QC STALE
```

Current downstream manifests are superseded, attempts reset, and the job returns to `READY`.

Explicit invalidation is also available for provider logic when an external input changes before a new manifest is written.

## Retry model

Each stage declares `max_attempts` from 1 to 20.

A retryable stage failure enters `WAITING_RETRY` only while attempts remain. Retry must be explicitly scheduled before the stage may run again.

This keeps retry behavior auditable and prevents accidental infinite loops.

## Source reconciliation

Before a stage starts or retry is scheduled, the contract re-checks:

- Build Proposal is still APPROVED;
- proposal revision is unchanged;
- source fingerprint is unchanged;
- Research Validation is still current;
- Product BUILD_READY is still current;
- asset class still matches.

If any check fails, the provider job becomes `STALE` and cannot continue.

## Resource and cost accounting

Providers append resource events such as:

- CPU seconds;
- GPU seconds;
- memory GB-seconds;
- storage GB-hours;
- model input/output tokens;
- network bytes;
- other measured units.

Each event may record an estimated USD cost. Job summaries aggregate the estimate without pretending that the estimate is a billing source of truth.

## API

Read:

- `GET /v1/production-providers`
- `GET /v1/production-provider-jobs`
- `GET /v1/production-provider-jobs/{job_id}`

Create:

- `POST /v1/production-provider-jobs`

The create API requires `X-Approval-Key`. Low-level stage mutation is intentionally not exposed as a public API in v0.1; provider workers use the internal contract functions.

## Persistence

Migration 032 adds:

- `production_provider_definitions`
- `production_provider_jobs`
- `production_provider_job_stages`
- `production_provider_manifests`
- `production_provider_resource_events`
- `production_provider_events`

## Shrimp Animation integration

The next provider can implement the contract as:

```text
provider_key: shrimp_animation
asset_class: CONTENT_IP

CONTENT_BRIEF / PLAN
  -> STORY / GENERATE
  -> SCRIPT / GENERATE
  -> STORYBOARD / GENERATE
  -> ASSETS / GENERATE
  -> VOICES / GENERATE
  -> ANIMATION / GENERATE
  -> RENDER / PACKAGE
  -> QC / QC
```

The provider will add its domain-specific manifests and selective invalidation logic on top of this generic contract.

## Safety boundary

Production Provider Contract v0.1 never:

- creates or approves a Build Proposal;
- bypasses the Human Build Approval gate;
- enables Production execution;
- calls the controlled Vercel Production executor;
- promotes or rolls back Production;
- enables publishing;
- posts content to external platforms.
