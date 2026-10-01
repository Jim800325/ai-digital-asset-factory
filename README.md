# AI Digital Asset Factory

Autonomous discovery, research, build-review, and controlled digital-asset production pipeline focused on **repeatable digital assets**, not client-service opportunities.

## Current project flow

```text
DISCOVERY
  -> CRAWL / INGEST
  -> EVIDENCE
  -> OPPORTUNITY
  -> CROSS-SOURCE AGGREGATION
  -> EVIDENCE QUALITY
  -> RESEARCH REPORT
  -> RESEARCH VALIDATION
  -> BUILD_READY
  -> BUILD_PROPOSAL
  -> PENDING_APPROVAL
  -> APPROVED / REJECTED
  -> ISOLATED SANDBOX EXECUTION
  -> ARTIFACT_READY
  -> RELEASE REVIEW PACKAGE
  -> LIVE VALIDATION
  -> HUMAN RELEASE GATE
```

Production deployment and publishing remain separately human-gated.

Asset classes:
- DATASET_API
- INTELLIGENCE_REPORT
- MICRO_SAAS_TOOL
- TEMPLATE_WORKFLOW
- CONTENT_IP

## v0.4 provider track: Shrimp Animation Factory

A new `CONTENT_IP` provider track is being designed for automated 2D story-animation production.

```text
STORY_SOURCE
  -> SCRIPT_READY
  -> STORYBOARD_READY
  -> ASSETS_READY
  -> VOICES_READY
  -> ANIMATION_READY
  -> RENDERED
  -> QC_PASSED
  -> PUBLISH_READY
  -> HUMAN_PUBLISH_APPROVAL
```

Target implementation stack:

- LLM / AIHubMix-compatible provider for story and scene planning
- structured `scene_manifest` as the contract between planning and rendering
- ComfyUI for character/background/prop asset generation
- pluggable TTS / GPT-SoVITS-compatible voice adapter
- Remotion for deterministic 2D animation and timeline rendering
- FFmpeg for final audio/subtitle/video assembly
- scheduler + PostgreSQL audit trail from the existing factory
- human gate before external publishing

The first version intentionally avoids full-frame generative video. Reusable character assets plus deterministic 2D motion are cheaper, easier to reproduce, and more suitable for unattended batch production.

See:
- `docs/PROJECT_FLOW_V04.md`
- `docs/SHRIMP_ANIMATION_PROVIDER_V01.md`

## Safety boundary

The repository uses explicit state transitions and human gates around execution and release. Approval of a research/build proposal does not automatically authorize production deployment or external publishing.

## Start

```bash
cp .env.example .env
docker compose up --build
```

Open http://localhost:8000/docs.
