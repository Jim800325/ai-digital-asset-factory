# Shrimp Animation Provider v0.1 — Step 10

## Goal

Step 10 introduces the controlled external-publish execution boundary after a
Step 9 `PUBLISH_AUTHORIZED` decision.

The implementation separates:

```text
authorization
    ↓
immutable execution snapshot
    ↓
one upload write
    ↓
read-only reconciliation if ambiguous
    ↓
one publish write
    ↓
read-only reconciliation if ambiguous
```

No automatic retry of a provider mutation is allowed.

## Source identity

A Controlled Publisher Execution is frozen to:

```text
publish_plan_id
authorization_decision_id
plan_sha256
dry_run_sha256
authorization_decision_sha256
review_decision_sha256
episode_bundle_sha256
release_review_package_sha256
render_artifact_sha256
target_snapshot_sha256
publish_metadata
platform
target_key
account_reference
execution_adapter
```

The execution snapshot has its own deterministic:

```text
execution_sha256
upload_idempotency_key
publish_idempotency_key
```

## State machine

```text
SNAPSHOT_CREATED
      |
      v
UPLOADING
  |       |
  |       +------> UPLOAD_UNKNOWN
  |                    |
  v                    | read-only reconcile
UPLOADED <--------------+
  |
  v
PUBLISHING
  |       |
  |       +------> PUBLISH_UNKNOWN
  |                     |
  v                     | read-only reconcile
PUBLISHED <--------------+
```

Terminal failure states are:

```text
UPLOAD_FAILED
PUBLISH_FAILED
```

## Exactly-once provider-write budgets

Each execution stores:

```text
upload_write_count
publish_write_count
```

Both are database constrained to:

```text
0 <= count <= 1
```

Before the provider call is made, the corresponding write count is atomically
set to 1 and the state moves to `UPLOADING` or `PUBLISHING`.

Therefore a process crash, timeout, connection reset, or lost response cannot
cause an automatic second provider write.

When count=1, another write request is rejected and reconciliation is required.

## Ambiguous writes

The provider adapter may raise:

```text
PublisherWriteOutcomeUnknown
```

The execution then persists:

```text
UPLOAD_UNKNOWN
or
PUBLISH_UNKNOWN
```

with immutable request SHA-256 and error evidence SHA-256.

The only legal recovery is:

```text
reconcile_upload()
reconcile_publish()
```

These methods are contractually read-only with respect to the provider.

A pending reconciliation leaves the state UNKNOWN. A confirmed provider object
moves the state forward without consuming another write.

## Source becomes stale during an ambiguous write

If Step 8/9 evidence becomes STALE after a provider write may already have
happened, Step 10 sets:

```text
source_stale = true
```

This blocks all new provider writes.

However read-only reconciliation remains allowed. This is intentional: once an
external mutation may have occurred, the system must still be able to determine
what happened and close the audit trail.

## Sacrificial account policy

Any non-MOCK controlled provider execution requires all of:

```text
SHRIMP_PUBLISH_EXECUTOR_ENABLED=true

SHRIMP_PUBLISH_EXECUTION_ALLOWED_ACCOUNT_REFS
SHRIMP_PUBLISH_EXECUTION_DENIED_ACCOUNT_REFS
SHRIMP_PUBLISH_EXECUTION_ALLOWED_TARGET_KEYS
SHRIMP_PUBLISH_EXECUTION_DENIED_TARGET_KEYS
```

The execution is rejected when:

- the account is denylisted;
- the target is denylisted;
- the account is not explicitly sacrificial-account allowlisted;
- the target is not explicitly sacrificial-target allowlisted;
- allowlist and denylist overlap;
- the real-account denylist is empty;
- the sacrificial allowlist is empty.

This means enabling the executor alone is insufficient.

## Independent execution key

Step 10 uses:

```text
SHRIMP_PUBLISH_EXECUTION_KEY
X-Shrimp-Publish-Execution-Key
```

It must be independent from:

- build approval key;
- release key;
- deployment authorization key;
- Production execution key;
- Shrimp Step 8 review key;
- Shrimp Step 9 publish authorization key.

A reused key is treated as MISCONFIGURED.

## Adapter contract

Step 10 defines a provider-neutral `PublisherExecutionAdapter` contract:

```text
validate_target
upload
reconcile_upload
publish
reconcile_publish
```

Built-in adapter kinds:

```text
MOCK
BILIBILI_CONTROLLED
YOUTUBE_CONTROLLED
```

The Bilibili and YouTube classes define the platform contract and target
validation boundary. They intentionally do not ship real account credentials or
an automatic live-publishing implementation.

CI uses an injected controlled fixture adapter to exercise real state-machine
semantics without network access.

A future provider implementation must satisfy this exact contract rather than
bypassing the Step 10 execution engine.

## Immutable execution audit

Migration 042 adds:

```text
shrimp_animation_publish_executions
shrimp_animation_publish_execution_events
```

Execution identity/content is immutable.

Provider upload IDs, provider publish IDs and publish URLs become immutable once
captured.

Write counts are monotonic and bounded to one.

Execution events are append-only.

## API

Snapshot:

```text
POST /v1/shrimp-animation/publish-plans/{plan_id}/execution
```

Upload:

```text
POST /v1/shrimp-animation/publish-executions/{execution_id}/upload
```

Upload reconciliation:

```text
POST /v1/shrimp-animation/publish-executions/{execution_id}/upload/reconcile
```

Publish:

```text
POST /v1/shrimp-animation/publish-executions/{execution_id}/publish
```

Publish reconciliation:

```text
POST /v1/shrimp-animation/publish-executions/{execution_id}/publish/reconcile
```

All mutation and reconciliation endpoints require the independent Step 10
execution key.

## CI acceptance

The Step 10 acceptance fixture deliberately simulates:

1. provider accepts Upload;
2. client loses the response;
3. execution enters `UPLOAD_UNKNOWN`;
4. blind second Upload is rejected;
5. read-only reconcile finds the uploaded object;
6. provider accepts Publish;
7. client loses the response;
8. execution enters `PUBLISH_UNKNOWN`;
9. blind second Publish is rejected;
10. upstream episode evidence becomes STALE;
11. no new provider write is permitted;
12. read-only reconciliation still confirms the already-created publish;
13. final audit state becomes `PUBLISHED` with `source_stale=true`.

The fixture records exactly:

```text
upload provider write calls = 1
publish provider write calls = 1
```

## Safety boundary

The repository still defaults to:

```text
SHRIMP_PUBLISH_EXECUTOR_ENABLED=false
SHRIMP_PUBLISH_EXECUTION_ADAPTER=MOCK
```

No real Bilibili or YouTube credentials are included.

CI makes no external publisher network calls.

A real sacrificial-account live acceptance therefore requires an explicitly
installed provider implementation plus dedicated test credentials and
allowlisted test targets. Production/main accounts remain denylisted.
