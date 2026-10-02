# Shrimp Animation Provider v0.1 — Step 5

## Goal

Step 5 is the first stage allowed to execute the Remotion renderer.

It consumes only the frozen Step 4 composition identity:

```text
animation_manifest_sha256
props_sha256
project_source_sha256
composition_record_id
```

No render is accepted when any frozen input drifts.

## State boundary

```text
ANIMATION SUCCEEDED
      |
      v
RENDER RUNNING
      |
      +-- Controlled Remotion subprocess (shell=false)
      |
      +-- independent MP4 SHA-256
      +-- ffprobe width / height
      +-- ffprobe FPS
      +-- ffprobe frame count
      +-- ffprobe duration
      +-- immutable provenance
      |
      v
RENDER SUCCEEDED
      |
      v
QC PENDING
```

Step 5 does not perform QC approval, publishing, Production deployment,
promotion, rollback, or external distribution.

## Controlled renderer

`ControlledRemotionRenderAdapter`:

- accepts only a local `file://` frozen props document;
- requires the props path to remain under the configured props root;
- recomputes and verifies the props SHA-256;
- recomputes and verifies the Remotion project source SHA-256;
- builds an argv list and always executes with `shell=False`;
- writes only inside the configured render output root;
- returns a file path, not trusted media metadata.

The stage independently probes the output after the renderer exits.

## Independent media verification

The RENDER stage computes the MP4 SHA-256 itself and invokes `ffprobe`
with frame counting enabled.

A render is rejected unless all fields match the frozen timeline:

- width
- height
- FPS
- total frame count
- duration within a two-frame / 50ms minimum tolerance
- MP4 container
- non-empty file

The immutable database record also stores the ffprobe payload and provenance.

## Invalidation

A CURRENT render belongs to one CURRENT Step 4 composition.

When the composition becomes STALE, a database trigger marks its render
artifact STALE in the same transaction. Upstream Content Brief invalidation
also clears `render_artifact_sha256` from the Shrimp job metadata.

## Runtime defaults

```text
SHRIMP_RENDER_ADAPTER=DISABLED
SHRIMP_REMOTION_RENDER_OUTPUT_ROOT=
SHRIMP_REMOTION_RENDER_TIMEOUT_SECONDS=300
SHRIMP_FFPROBE_CLI=ffprobe
```

A worker must explicitly opt in with `SHRIMP_RENDER_ADAPTER=REMOTION`.

## CI acceptance

CI covers both layers separately:

1. Full provider state-machine acceptance using a local FFmpeg video fixture.
   This proves RENDER status transitions, MP4 verification, immutable storage,
   replay idempotency, QC remaining PENDING, and upstream stale propagation.
2. A real Remotion CLI smoke render using the pinned renderer dependency.
   It renders a short 320x240 MP4 and passes the same independent ffprobe
   verifier used by the provider stage.

The real smoke is intentionally tiny so CI validates the renderer itself
without pretending to be a production content render.

## Safety boundary

Step 5 keeps these invariants unchanged:

```text
production_execution_enabled = false
publish_enabled = false
Production promotion = false
Production rollback = false
external publishing = false
```

The next stage is Step 6: QC / media quality and content-integrity acceptance.
