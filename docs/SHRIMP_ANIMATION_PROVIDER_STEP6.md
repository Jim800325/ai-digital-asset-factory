# Shrimp Animation Provider v0.1 — Step 6

## Goal

Step 6 converts a verified Step 5 MP4 into a deterministic, auditable QC decision.

It consumes only current, hash-bound artifacts:

```text
animation timeline manifest
render artifact manifest
render database record
MP4 file bytes
Step 5 provenance
```

A passed QC report never publishes or deploys anything.

## State boundary

```text
RENDER SUCCEEDED
      |
      v
QC RUNNING
      |
      +-- lineage / artifact SHA verification
      +-- ffprobe stream inventory
      +-- width / height / FPS / frame count / duration
      +-- scene / dialogue / subtitle timeline integrity
      +-- black-frame detection
      +-- freeze-frame detection
      +-- expected-dialogue silence analysis
      |
      v
QC SUCCEEDED
job_status = QC_PASSED
      |
      v
PACKAGING_REVIEW (not executed by Step 6)
```

Any hard check failure causes QC to fail closed.

## Timeline integrity checks

Step 6 revalidates the frozen animation timeline rather than trusting the
renderer output alone.

Hard checks include:

- scene ranges remain contiguous;
- dialogue line IDs are unique;
- every audio cue has exactly one aligned subtitle;
- subtitle and audio line-ID sets are a bijection;
- dialogue windows remain inside the total animation duration.

The subtitle check is timeline-contract verification. Remotion burns subtitles
into video pixels, so Step 6 does not use OCR to infer text from rendered
frames.

## Media verification

`ffprobe` is used to inspect:

- video stream count;
- audio stream presence when dialogue is expected;
- MP4 duration;
- resolution;
- frame rate;
- exact decoded frame count.

The MP4 SHA-256 is recomputed again immediately before QC and on QC replay.

## Visual quality gates

FFmpeg decodes the rendered video and runs:

```text
blackdetect
freezedetect
```

Default hard thresholds:

```text
max black segment   = 1500 ms
max black ratio     = 10%
max freeze segment  = 8000 ms
max freeze ratio    = 50%
```

These thresholds are deterministic configuration, not subjective visual
scoring.

## Dialogue audio gate

When the timeline contains dialogue, Step 6 requires at least one audio stream.

FFmpeg runs `silencedetect`, then silence is evaluated only inside the
expected dialogue windows from the frozen timeline.

Default:

```text
max silence ratio inside any expected dialogue window = 80%
```

This avoids rejecting intentional silence between dialogue lines while still
rejecting silent expected speech.

## Immutable QC reports

Migration 038 adds immutable QC audit records bound to:

```text
render_id
render_artifact_sha256
animation_manifest_sha256
analyzer_version
report_sha256
report_content
```

Passed reports are CURRENT/PASSED. If their render becomes STALE, the report is
automatically marked STALE and `qc_report_sha256` is cleared from the Shrimp
job metadata.

Failed QC attempts are retained as immutable FAILED audit records.

## Runtime defaults

```text
SHRIMP_QC_ANALYZER=DISABLED
SHRIMP_QC_FFMPEG_CLI=ffmpeg
SHRIMP_QC_MAX_BLACK_SEGMENT_MS=1500
SHRIMP_QC_MAX_BLACK_RATIO=0.10
SHRIMP_QC_MAX_FREEZE_SEGMENT_MS=8000
SHRIMP_QC_MAX_FREEZE_RATIO=0.50
SHRIMP_QC_MAX_DIALOGUE_SILENCE_RATIO=0.80
SHRIMP_QC_ANALYSIS_TIMEOUT_SECONDS=180
```

An internal worker must explicitly enable:

```text
SHRIMP_QC_ANALYZER=FFMPEG
```

## Acceptance strategy

The Step 6 acceptance test builds a short full provider job and renders a real
MP4 with:

- moving video frames;
- an AAC audio stream;
- exact frame count and duration.

It then verifies successful QC and injects independent failure cases:

- subtitle/audio line-ID mismatch;
- black + silent media;
- wrong resolution;
- render lineage hash drift;
- attempted mutation of an immutable passed QC report;
- upstream content change after QC.

Successful completion must leave:

```text
RENDER = SUCCEEDED
QC = SUCCEEDED
job_status = QC_PASSED
publish_enabled = false
production_execution_enabled = false
```

## Safety boundary

Step 6 does not create a release candidate, package for distribution, publish,
deploy, promote, roll back, or change Production traffic.

The next independent stage is Packaging / Release Review.
