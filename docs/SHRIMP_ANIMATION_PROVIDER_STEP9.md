# Shrimp Animation Provider v0.1 — Step 9

## Goal

Step 9 adds a controlled publishing authorization boundary after Step 8 human
release approval.

It does not publish anything.

The Step 9 authorization identity is bound to all three frozen Step 8 values:

```text
review_decision_sha256
episode_bundle_sha256
release_review_package_sha256
```

A platform-specific Publisher Plan adds:

```text
target_snapshot_sha256
dry_run_sha256
plan_sha256
```

## State flow

```text
RELEASE_APPROVED
      |
      v
Platform Target Registry
      |
      v
Controlled Publisher Plan
      |
      v
Dry-Run VERIFIED
      |
      v
PENDING_AUTHORIZATION
      |
      +-- AUTHORIZE --> PUBLISH_AUTHORIZED
      |
      +-- REJECT ----> PUBLISH_REJECTED
```

Both terminal states preserve:

```text
execution_enabled = false
publish_performed = false
external publish performed = false
network requests = 0
credential access = 0
external writes = 0
```

## Platform Target Registry

Step 9 adds:

```text
shrimp_animation_publish_targets
```

A target stores only non-secret routing metadata:

- target_key
- platform
- display name
- optional account/channel reference
- local metadata guardrails
- ACTIVE / INACTIVE status

Supported platform identifiers in v0.1:

```text
BILIBILI
YOUTUBE
CUSTOM
```

The registry does not accept or store:

- access tokens
- cookies
- passwords
- refresh tokens
- API secrets
- publisher credentials

Database constraints force:

```text
execution_enabled = false
external_publish_enabled = false
```

## Local metadata guardrails

Target metadata constraints are internal Step 9 dry-run guardrails. They are not
claims about any platform's current public API limits.

The target can constrain:

- max title characters
- max description characters
- max tags
- max tag characters
- allowed visibility values
- whether category is required

The normalized Publisher Plan metadata can contain:

- title
- description
- tags
- category
- visibility
- cover_artifact_sha256
- scheduled_for

## Publisher Plan

Step 9 adds:

```text
shrimp_animation_publish_plans
```

Every Plan captures:

- provider job ID
- current Step 8 APPROVE decision ID
- review_decision_sha256
- episode_bundle_sha256
- release_review_package_sha256
- platform target ID and target snapshot SHA-256
- normalized publish metadata
- dry-run snapshot and SHA-256
- immutable Plan payload and SHA-256
- authorization lifecycle state

Only one non-STALE Plan may exist for the same Episode + target.

If the same deterministic request is replayed, the existing Plan is returned.
A different metadata request is rejected until the old Plan becomes STALE.

## Dry-run verification

Plan creation re-verifies the current Step 8 artifacts before creating the
Plan:

- Episode Bundle bytes
- Review Markdown bytes
- rendered Episode MP4 bytes
- Step 8 hash binding
- current APPROVE decision
- RELEASE_APPROVED status
- QC_PASSED
- external_side_effects=DENY

The deterministic dry-run snapshot includes:

```text
network_request_count = 0
credential_access_count = 0
external_write_count = 0
execution_enabled = false
external_publish_enabled = false
publish_performed = false
external_side_effects = DENY
```

Step 9 contains no platform client and no publisher credential.

## Publish authorization key

Step 9 uses a separate key:

```text
SHRIMP_PUBLISH_AUTHORIZATION_KEY
X-Shrimp-Publish-Key
```

The key must be independent from:

- build approval key
- software release key
- deployment authorization key
- Production execution key
- Shrimp Step 8 human review key

If the key is reused, the Step 9 gate reports MISCONFIGURED and refuses
authorization.

The browser does not persist the key.

## Authorization

AUTHORIZE requires the exact current:

```text
plan_sha256
dry_run_sha256
```

Immediately before authorization Step 9 re-hashes the Step 8 files again.

The database transaction then verifies:

- Plan is PENDING_AUTHORIZATION
- dry-run status is VERIFIED
- target is still ACTIVE
- target snapshot SHA-256 is unchanged
- Step 8 review decision is still CURRENT APPROVE
- review_decision_sha256 is unchanged
- Episode Bundle SHA-256 is unchanged
- Release Review Package SHA-256 is unchanged
- job remains QC_PASSED
- job remains RELEASE_APPROVED
- external_side_effects remains DENY
- publisher execution remains disabled

A legal authorization produces:

```text
plan_status = PUBLISH_AUTHORIZED
next_stage = CONTROLLED_PUBLISHER_EXECUTION

execution_enabled = false
publish_performed = false
external_publish_performed = false
```

Step 9 does not implement CONTROLLED_PUBLISHER_EXECUTION.

## Rejection

REJECT produces:

```text
plan_status = PUBLISH_REJECTED
next_stage = NONE
```

It does not execute any external action.

## Immutable authorization ledger

Step 9 adds:

```text
shrimp_animation_publish_authorization_decisions
```

Every authorization decision records:

- Publisher Plan ID
- AUTHORIZE / REJECT
- reason
- actor
- Plan SHA-256
- dry-run SHA-256
- deterministic decision SHA-256
- CURRENT / STALE lifecycle state
- decision timestamp

Decision identity/content is immutable.

## Authorization block audit

Step 9 also adds:

```text
shrimp_animation_publish_authorization_blocks
```

If AUTHORIZE encounters current-state drift, the block records:

- blocking reasons
- current review decision SHA-256
- current target snapshot SHA-256
- current dry-run SHA-256

No external publisher action occurs.

## Invalidation

If Step 8 review evidence becomes STALE:

```text
Step 8 Review Decision STALE
        |
        v
Publisher Plan STALE
        |
        v
Publish Authorization Decision STALE
```

The old authorization remains available as historical audit evidence.

If a Platform Target definition changes, all non-STALE Plans for that target
also become STALE.

A fresh Plan must be generated and independently authorized.

## UI

The Step 9 workspace is available at:

```text
/animation-publishing
/animation-publishing/{job_id}
```

The workspace provides:

1. Platform Target Registry
2. RELEASE_APPROVED Episode selection
3. Publisher metadata planning
4. deterministic dry-run inspection
5. explicit AUTHORIZE / REJECT

There is intentionally no Upload, Publish, Promote, or Execute button.

## Safety boundary

Step 9 does not:

- call Bilibili APIs
- call YouTube APIs
- read platform credentials
- upload a video
- publish an Episode
- schedule a real platform post
- change external account state
- enable a publisher executor

The next independent boundary after PUBLISH_AUTHORIZED is:

```text
Controlled Publisher Execution
```

That future stage must introduce its own execution key, provider adapters,
allowlists, idempotency/reconciliation rules and real external-write acceptance.
