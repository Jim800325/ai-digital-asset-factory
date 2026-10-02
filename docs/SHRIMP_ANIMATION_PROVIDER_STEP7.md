# Shrimp Animation Provider v0.1 — Step 7

## Goal

Step 7 packages a QC-passed episode into an immutable Episode Artifact Bundle
and a human-readable Release Review Package.

It does not approve or publish the episode.

## State boundary

```text
QC SUCCEEDED
job_status = QC_PASSED
      |
      v
PACKAGE RUNNING
      |
      +-- verify MP4 bytes and QC lineage
      +-- build deterministic Episode Bundle ZIP
      +-- build immutable review snapshot
      +-- build human-readable review Markdown
      |
      v
PACKAGE SUCCEEDED
job_status = QC_PASSED
review_status = READY_FOR_HUMAN_REVIEW
```

The global provider job keeps the existing QC_PASSED status. Animation-specific
human-review readiness is stored separately as READY_FOR_HUMAN_REVIEW.

## Episode Artifact Bundle

The deterministic ZIP contains:

```text
episode.mp4
timeline.json
subtitles.srt
asset_provenance.json
voice_provenance.json
qc_report.json
manifest_hashes.json
bundle_manifest.json
```

ZIP entries use:

- fixed 1980-01-01 timestamps;
- sorted paths;
- ZIP_STORED;
- fixed file permissions.

This prevents filesystem timestamps and compression implementation details from
changing the bundle hash.

The bundle manifest lists the seven payload artifacts and their:

- relative path;
- SHA-256;
- byte size;
- media type.

The bundle manifest itself has an independent SHA-256.

## Subtitles

subtitles.srt is generated directly from the frozen animation timeline.

Frame-to-time conversion is deterministic and uses the timeline FPS. No OCR is
used.

## Provenance

Asset and voice provenance files retain:

- logical key;
- artifact kind;
- source mode;
- media type;
- byte size;
- SHA-256;
- license ID;
- usage rights;
- adapter provenance;
- measured voice duration where applicable.

The review package explicitly summarizes whether all usage rights are APPROVED.

## Manifest lineage

manifest_hashes.json records every current provider manifest before PACKAGE,
including:

- CONTENT_BRIEF
- STORY
- SCRIPT
- SCENE
- ASSETS
- VOICES
- ANIMATION
- RENDER
- QC

It also records the current render artifact and QC report SHA-256 values.

## Release Review Package

The animation-specific review package is separate from the repository's
software/sandbox release_review_packages table.

The Shrimp review snapshot includes:

- episode metadata;
- Episode Bundle SHA-256 and artifact inventory;
- MP4 media facts;
- QC result and every QC check;
- asset provenance;
- voice provenance;
- current manifest hashes;
- full dialogue transcript;
- required human-review checklist;
- explicit safety state.

A deterministic Markdown document is generated so the package can be reviewed
without a custom UI.

Review snapshot hashing intentionally excludes absolute worker filesystem paths.
The bundle filename and cryptographic identity are included instead.

## Human-review checklist

The Step 7 package requires later human confirmation for:

1. full episode watch-through;
2. dialogue and subtitle correctness;
3. visual continuity and character consistency;
4. asset and voice rights/provenance;
5. explicit release intent.

These fields are PENDING review data only. Step 7 never marks them approved.

## Immutable database records

Migration 039 adds:

```text
shrimp_animation_episode_bundles
shrimp_animation_release_review_packages
```

Episode bundle identity and content are immutable after insertion.

Release Review Package identity and content are immutable after insertion.

Only lifecycle state may move from current/ready to STALE.

## Replay protection

A PACKAGE replay recomputes and verifies:

- Episode Bundle file SHA-256;
- human review Markdown SHA-256.

If either file has been replaced, deleted, or modified, replay fails closed.

## Invalidation

When a previously passed QC report becomes STALE:

```text
QC STALE
   ↓
PACKAGE STALE
   ↓
Episode Bundle STALE
   ↓
Review Package STALE
   ↓
episode_bundle_sha256 = NULL
release_review_package_sha256 = NULL
review_status = STALE
```

Historical immutable records remain available for audit.

## Runtime defaults

```text
SHRIMP_PACKAGE_ENABLED=false
SHRIMP_PACKAGE_OUTPUT_ROOT=
```

An internal worker must explicitly enable packaging.

## Safety boundary

Step 7 keeps:

```text
publish_enabled = false
production_execution_enabled = false
external_side_effects = DENY
automatic release approval = false
Production deployment = false
Production promotion = false
Production rollback = false
```

The next independent stage is a Human Review Workspace / explicit human
decision gate.
