# Project Flow v0.4

## Purpose

v0.4 extends AI Digital Asset Factory from opportunity discovery and controlled software/artifact generation into a provider-based digital-asset production platform.

The existing research, proposal, sandbox, review, and human-release controls remain authoritative. The new animation workflow is a downstream `CONTENT_IP` provider and must not bypass those controls.

## Global state flow

```text
DISCOVERY
  -> INGEST
  -> EVIDENCE
  -> OPPORTUNITY
  -> AGGREGATED
  -> EVIDENCE_QUALIFIED
  -> RESEARCH_GENERATED
  -> VALIDATED / PARTIAL / UNKNOWN
  -> BUILD_READY
  -> BUILD_PROPOSAL
  -> PENDING_APPROVAL
  -> APPROVED / REJECTED
  -> SANDBOX_REQUEST
  -> SANDBOX_EXECUTION
  -> ARTIFACT_READY
  -> REVIEW_PACKAGE
  -> WAITING_LIVE_VALIDATION
  -> READY_FOR_REVIEW
  -> RELEASE_APPROVED / RELEASE_REJECTED
```

Existing fail-closed behavior remains unchanged:

- research qualification is required before `BUILD_READY`
- build proposals require explicit approval
- sandbox execution is isolated from production credentials
- release review is separate from build approval
- deployment/publishing must not be inferred from `RELEASE_APPROVED`
- external publishing requires its own human decision

## Provider architecture

v0.4 introduces a provider boundary after a digital asset has a validated production definition.

```text
Validated Digital Asset
        |
        v
Provider Registry
        |
        +-- MICRO_SAAS_TOOL provider
        +-- DATASET_API provider
        +-- INTELLIGENCE_REPORT provider
        +-- TEMPLATE_WORKFLOW provider
        +-- CONTENT_IP provider
                |
                +-- shrimp_animation
```

A provider owns domain-specific planning, asset generation, rendering, validation, and artifact manifests. It does not own release authorization.

## Shrimp Animation production state machine

The first `CONTENT_IP` provider is `shrimp_animation`.

```text
CONTENT_BRIEF
  -> STORY_READY
  -> SCRIPT_READY
  -> STORYBOARD_READY
  -> ASSETS_READY
  -> VOICES_READY
  -> ANIMATION_READY
  -> RENDERED
  -> QC_PASSED
  -> PUBLISH_READY
  -> HUMAN_PUBLISH_APPROVAL
  -> PUBLISHED
```

Failure/retry states should be stage-local and resumable. A failed voice render must not force regeneration of approved character assets. A changed script invalidates downstream storyboard, voice, animation, render, and QC outputs.

## Artifact contracts

The provider should make each stage deterministic and auditable through structured manifests.

Core contracts:

1. `content_brief.json`
   - title / premise
   - audience
   - episode length target
   - style profile
   - source attribution / provenance
   - policy constraints

2. `script.json`
   - scenes
   - dialogue
   - narration
   - characters
   - estimated duration

3. `scene_manifest.json`
   - background
   - character placements
   - expressions
   - actions
   - camera operations
   - transitions
   - dialogue timing
   - sound effects
   - subtitle cues

4. `asset_manifest.json`
   - character assets
   - background assets
   - props
   - hashes
   - generation parameters
   - license/provenance metadata

5. `voice_manifest.json`
   - speaker mapping
   - voice provider
   - audio files
   - duration
   - hashes

6. `render_manifest.json`
   - renderer version
   - scene versions
   - frame rate
   - resolution
   - output hashes

7. `qc_report.json`
   - missing assets
   - audio/video duration mismatch
   - clipping
   - subtitle overflow
   - silent segments
   - duplicate frames
   - failed render frames
   - policy/provenance checks

## Automation model

The scheduler can autonomously advance only states whose prerequisites are satisfied.

Example daily run:

```text
scheduler
  -> select eligible CONTENT_IP jobs
  -> generate/update story
  -> plan scenes
  -> generate missing reusable assets
  -> synthesize voices
  -> render deterministic animation
  -> run QC
  -> stop at PUBLISH_READY
```

External publishing remains human-gated in v0.4.

## Resource strategy

The default v0.4 animation path is optimized for modest local GPUs:

- ComfyUI generates reusable still assets and limited motion assets.
- Remotion handles deterministic transforms, keyframes, camera motion, subtitles, and timing.
- FFmpeg handles final muxing, audio mix, encoding, and packaging.
- Full-frame diffusion/video generation is optional and not a v0.4 dependency.

This keeps the first production loop reproducible and suitable for continuous unattended execution.

## Implementation order

### Step 1 — Provider contracts
- provider registry interface
- animation job model
- state transitions
- `scene_manifest` schema
- invalidation rules

### Step 2 — Deterministic renderer
- character/background registry
- action primitives
- camera primitives
- subtitle renderer
- Remotion adapter
- FFmpeg adapter

### Step 3 — Voice layer
- provider-neutral TTS interface
- GPT-SoVITS-compatible adapter
- speaker/voice registry
- duration alignment

### Step 4 — Asset generation
- ComfyUI adapter
- reusable character packs
- expression/action variants
- provenance and hashes

### Step 5 — QC and scheduler
- stage-level resumability
- deterministic reruns
- automated QC
- batch scheduling
- cost/resource accounting

### Step 6 — Publish gate
- publication package
- title/description/cover artifacts
- explicit human publish approval
- platform-specific publisher adapters only after acceptance

## Non-goals for v0.4

- automatic public publishing without a human gate
- copying a real creator's proprietary character pack
- cloning real people's voices without rights
- requiring high-VRAM generative-video models for the baseline workflow
- making ComfyUI responsible for the entire animation timeline
