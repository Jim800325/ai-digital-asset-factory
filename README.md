# AI Digital Asset Factory

AI Digital Asset Factory is an autonomous discovery, research, build-review, and controlled digital-asset production system focused on repeatable assets rather than one-off client work.

## Current project flow

```text
SOURCE / MARKET DISCOVERY
        |
        +--> GitHub Side-Business Provider Discovery
        |      -> Provider Registry
        |      -> License / Activity / Commercial Scoring
        |      -> Provider BUILD_READY Queue
        |      -> Opportunity Evidence Ingestion
        |
        v
EVIDENCE COLLECTION
  -> CROSS-SOURCE AGGREGATION
  -> EVIDENCE QUALITY GATE
  -> OPPORTUNITY
  -> RESEARCH REPORT
  -> RESEARCH VALIDATION
  -> PRODUCT BUILD_READY
  -> BUILD PROPOSAL
  -> HUMAN BUILD APPROVAL
  -> ISOLATED SANDBOX / PROVIDER EXECUTION
  -> ARTIFACT_READY
  -> RELEASE REVIEW PACKAGE
  -> LIVE VALIDATION
  -> HUMAN RELEASE GATE
  -> DEPLOYMENT AUTHORIZATION
  -> EXECUTION INTEGRITY GATE
  -> HUMAN PRODUCTION EXECUTION GATE
  -> CONTROLLED PREPARE
  -> READY_FOR_PROMOTION
  -> HUMAN PROMOTION DECISION
```

Provider BUILD_READY and Product BUILD_READY are different states. A repository becoming a strong technical/commercial building block does not authorize a product build or deployment.

## Provider tracks

Validated digital assets may use provider-specific production pipelines:

- `MICRO_SAAS_TOOL`
- `DATASET_API`
- `INTELLIGENCE_REPORT`
- `TEMPLATE_WORKFLOW`
- `CONTENT_IP`

The first `CONTENT_IP` production provider is the planned `shrimp_animation` workflow:

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

Baseline stack:

- AIHubMix-compatible LLM provider for story/script/scene planning
- structured manifests as deterministic stage contracts
- ComfyUI for reusable characters, backgrounds, props, and covers
- provider-neutral TTS / GPT-SoVITS-compatible voice adapter
- Remotion for deterministic 2D animation and timeline rendering
- FFmpeg for audio mix, muxing, encoding, and final verification
- PostgreSQL audit/state storage and scheduler orchestration
- human gate before external publishing

## Safety boundary

The system is fail-closed around release and production execution.

Automatic discovery, scoring, research refresh, provider re-evaluation, sandbox work, rendering, and QC may run unattended when prerequisites are satisfied. Build approval, production release approval, deployment authorization, production promotion, rollback, and external publishing remain separately gated.

## Key documents

- `docs/PROJECT_FLOW_V05.md`
- `docs/side-business-provider-registry-v0.1.md`
- `docs/SHRIMP_ANIMATION_PROVIDER_V01.md`
- `docs/controlled-production-release-executor-v0.1.md`
- `docs/vercel-prepare-live-acceptance-v0.1.md`

## Start

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:8000/docs`.
