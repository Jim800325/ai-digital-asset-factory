# Shrimp Animation Provider v0.1 — Step 8

## Goal

Step 8 adds the human decision boundary after deterministic packaging.

It consumes only the frozen Step 7 identities:

```text
episode_bundle_sha256
release_review_package_sha256
```

The reviewer can watch the verified Episode, inspect QC and provenance, inspect
the Bundle contents and manifest hashes, then explicitly choose APPROVE or
REJECT.

APPROVE does not publish anything.

## State boundary

```text
PACKAGE SUCCEEDED
QC_PASSED
READY_FOR_HUMAN_REVIEW
        |
        v
Human Review Workspace
        |
        +-- Episode Player
        +-- QC inspection
        +-- Asset / voice provenance inspection
        +-- Bundle / manifest hash inspection
        +-- Transcript inspection
        +-- required checklist
        |
        +---- APPROVE ---> RELEASE_APPROVED
        |
        +---- REJECT ----> RELEASE_REJECTED
```

Both terminal decisions keep:

```text
publish_enabled = false
production_execution_enabled = false
external_side_effects = DENY
```

## Independent review gate

Step 8 uses:

```text
SHRIMP_HUMAN_REVIEW_KEY
X-Shrimp-Review-Key
```

This key is independent from the repository's software release key and
deployment authorization keys.

The browser never persists the review key.

## Workspace

The UI is available at:

```text
/animation-review
/animation-review/{job_id}
```

It exposes six review surfaces:

1. Episode Player
2. QC
3. Provenance
4. Artifacts / Hashes
5. Transcript
6. APPROVE / REJECT

The workspace API never exposes internal file:// worker paths.

## Episode Player integrity

Before the UI considers APPROVE available, the backend recomputes the current
MP4 SHA-256 and requires it to match all of:

```text
shrimp_animation_renders.artifact_sha256
Review Package media.render_artifact_sha256
Episode Bundle artifact_manifest["episode.mp4"].sha256
Episode Bundle render_artifact_sha256
```

The media endpoint performs the same verification again immediately before
serving the MP4.

If the player source is modified, APPROVE becomes blocked. REJECT remains
available so a reviewer can explicitly reject corrupted or unavailable media.

## Frozen identity binding

Every decision request must provide the exact current:

```text
episode_bundle_sha256
release_review_package_sha256
```

The database transaction locks the Shrimp job and compares these values again
before inserting the decision.

Any drift causes the decision to fail closed.

## APPROVE checklist

APPROVE requires all five Step 7 checklist items:

```text
watch_full_episode
verify_dialogue_and_subtitles
verify_visual_continuity
verify_rights_and_provenance
approve_release_intent
```

APPROVE additionally requires:

- QC status PASSED;
- zero hard QC failures;
- current Bundle and Review Package;
- Bundle bytes verified;
- Review Markdown bytes verified;
- Episode Player bytes verified;
- asset usage rights approved;
- voice usage rights approved;
- publish disabled;
- Production execution disabled;
- external side effects DENY.

## REJECT behavior

REJECT requires a current frozen Bundle and Review Package identity, but it does
not require all checklist items.

REJECT is deliberately allowed when media integrity has failed. This means a
human can record a terminal rejection for a corrupted review candidate rather
than being unable to act.

## Immutable decision ledger

Migration 040 adds:

```text
shrimp_animation_review_decisions
```

Every decision records:

- provider job;
- Episode Bundle ID;
- Review Package ID;
- APPROVE or REJECT;
- reason;
- reviewer actor;
- Episode Bundle SHA-256;
- Review Package SHA-256;
- confirmed checklist;
- deterministic decision SHA-256;
- CURRENT / STALE lifecycle status;
- decision timestamp.

Decision identity/content cannot be modified after insertion.

The lifecycle fields may transition from CURRENT to STALE when upstream evidence
changes.

## Terminal states

APPROVE produces:

```text
review_status = RELEASE_APPROVED
next_stage = PUBLISHING_AUTHORIZATION
publish_enabled = false
```

REJECT produces:

```text
review_status = RELEASE_REJECTED
next_stage = NONE
publish_enabled = false
```

Step 8 contains no publisher.

## PACKAGE replay safety

Once a human decision is terminal, replaying the already-succeeded PACKAGE
stage must preserve:

```text
RELEASE_APPROVED
or
RELEASE_REJECTED
```

PACKAGE replay can no longer reset a terminal review state back to
READY_FOR_HUMAN_REVIEW.

## Invalidation

When upstream content changes:

```text
QC STALE
   |
PACKAGE STALE
   |
Bundle STALE
   |
Review Package STALE
   |
Human Decision STALE
   |
review_status = STALE
reviewed_at = NULL
```

The old decision remains as historical evidence with decision_status=STALE.

A newly rebuilt Bundle can later receive a new independent human decision.

## Safety boundary

Step 8 does not:

- publish to Bilibili or another platform;
- upload externally;
- enable a publisher credential;
- create a Production deployment;
- promote Production traffic;
- roll back Production traffic;
- enable production execution.

The next independent boundary after RELEASE_APPROVED is Publishing
Authorization / Controlled Publisher.
