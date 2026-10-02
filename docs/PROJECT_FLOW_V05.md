# Project Flow v0.5

## Purpose

v0.5 unifies three previously separate tracks into one governed pipeline:

1. opportunity discovery and evidence-based research;
2. continuous side-business provider discovery and composition;
3. provider-specific digital-asset production such as automated story animation.

The existing build, release, deployment, and human production gates remain authoritative. No provider may bypass them.

## Global architecture

```text
PUBLIC / INTERNAL SIGNALS
        |
        +-----------------------------+
        |                             |
        v                             v
Opportunity Discovery        GitHub Provider Discovery
        |                             |
        v                             v
Evidence Ingestion           Side-Business Provider Registry
        |                             |
        |                     License / Activity / Commercial
        |                             |
        |                     Provider BUILD_READY Queue
        |                             |
        +-------------<---------------+
                      |
                      v
            Cross-Source Aggregation
                      |
                      v
              Evidence Quality Gate
                      |
                      v
                 Opportunity
                      |
                      v
               Research Report
                      |
                      v
             Research Validation
                      |
                      v
             Product BUILD_READY
                      |
                      v
               Build Proposal
                      |
                      v
             Human Build Approval
                      |
                      v
        Isolated Sandbox / Provider Run
                      |
                      v
                Artifact Ready
                      |
                      v
             Release Review Package
                      |
                      v
                Live Validation
                      |
                      v
             Human Release Gate
                      |
                      v
          Deployment Authorization
                      |
                      v
          Execution Integrity Gate
                      |
                      v
      Human Production Execution Gate
                      |
                      v
             Controlled PREPARE
                      |
                      v
           READY_FOR_PROMOTION
                      |
                      v
          Human Promotion Decision
```

For externally published content, publication remains a separate downstream decision after the production artifact is ready.

## Layer 1 — Side-Business Provider Registry

The provider registry continuously evaluates open-source repositories as technical/commercial building blocks.

```text
GitHub discovery
  -> metadata refresh
  -> license classification
  -> activity / popularity / maintenance analysis
  -> automation-fit score
  -> monetization-fit score
  -> RESEARCH / WATCH / BUILD_READY / BLOCKED
  -> provider queue
```

Default deterministic provider score:

- license / commercial usability: 30%
- recent development activity: 20%
- GitHub popularity: 15%
- maintenance pressure: 10%
- automation fit: 15%
- monetization signals: 10%

Provider `BUILD_READY` only means the repository is suitable to consider as a building block. It does not mean a product is validated or approved to build.

Every daily cycle can both promote and downgrade a provider. A previously qualified provider becomes stale when its evidence no longer satisfies the gate.

## Layer 2 — Provider Composition Planner

The next provider-registry layer is a composition planner.

Its job is to combine compatible provider roles into evidence-backed candidate stacks, for example:

```text
Discovery
  + Collection
  + Intelligence
  + Automation
  + Distribution
  -> Side-Business Hypothesis
```

A composition is not a build authorization. It becomes new opportunity evidence and must enter the normal cross-source research pipeline.

Suggested composition contract:

```json
{
  "composition_id": "stack-...",
  "roles": {
    "discovery": "...",
    "collection": "...",
    "intelligence": "...",
    "automation": "...",
    "distribution": "..."
  },
  "provider_versions": {},
  "license_compatibility": "PASS",
  "commercial_constraints": [],
  "estimated_operating_cost": {},
  "evidence_refs": [],
  "hypothesis": {}
}
```

## Layer 3 — Opportunity Research

The product opportunity still requires independent market validation even when its technical stack is excellent.

Required qualification remains:

- at least 2 independent source domains;
- Evidence Quality >= 65;
- Source Diversity >= 60;
- Signal Strength >= 55;
- opportunity score >= 75;
- buyer validation;
- competitor validation;
- pricing evidence;
- willingness-to-pay evidence;
- market-gap evidence;
- Research Validation completeness >= 75.

Only after these gates may the product-level `build_readiness` become `BUILD_READY`.

## Layer 4 — Build and sandbox execution

```text
Product BUILD_READY
  -> Build Proposal
  -> PENDING_APPROVAL
  -> APPROVED / REJECTED
  -> Sandbox Request
  -> Sandbox Execution
  -> Artifact Ready
```

The sandbox must remain isolated from production credentials and external production targets.

Provider-specific build execution may run inside this layer as long as it emits deterministic, reviewable artifacts and never treats build approval as production authorization.

## Layer 5 — Provider-specific digital-asset production

A validated digital asset is routed to a provider by asset class.

```text
Validated Digital Asset
        |
        v
Production Provider Registry
        |
        +-- MICRO_SAAS_TOOL
        +-- DATASET_API
        +-- INTELLIGENCE_REPORT
        +-- TEMPLATE_WORKFLOW
        +-- CONTENT_IP
                |
                +-- shrimp_animation
```

A production provider owns planning, stage execution, manifests, retries, invalidation, QC, and output packaging. It does not own release authorization.

## Shrimp Animation provider state machine

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

The provider uses structured contracts:

- `content_brief.json`
- `script.json`
- `scene_manifest.json`
- `asset_manifest.json`
- `voice_manifest.json`
- `render_manifest.json`
- `qc_report.json`

A failed stage resumes locally. A changed upstream artifact invalidates only its dependent downstream stages where practical.

Recommended baseline:

- LLM / AIHubMix-compatible story and scene planning
- ComfyUI for reusable characters, backgrounds, props, covers, and limited motion assets
- provider-neutral TTS with a GPT-SoVITS-compatible adapter
- Remotion for deterministic sprite/timeline animation
- FFmpeg for final audio/video packaging
- PostgreSQL for job state, provenance, hashes, and audit history

The baseline intentionally avoids full-frame diffusion video as a hard dependency.

## Layer 6 — Release and production execution

Artifact creation does not imply release approval.

```text
ARTIFACT_READY
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

The controlled executor remains fail-closed:

- no silent provider write replay;
- real Production targets stay denylisted during sacrificial acceptance;
- ambiguous PREPARE states reconcile with GET-only logic;
- promotion and rollback require separate authorization;
- production traffic cannot change merely because a candidate deployment is READY.

## Layer 7 — Publishing gate

For content assets, successful render and QC stop at `PUBLISH_READY`.

A publish package may contain:

- final media artifact;
- title and description candidates;
- cover;
- subtitles;
- platform metadata;
- license/provenance summary;
- QC report;
- content hashes.

External publishing requires an explicit human decision. Publisher adapters are downstream of that gate.

## Daily autonomous loop

```text
scheduler
  -> refresh provider registry
  -> discover new providers
  -> re-score and downgrade/promote providers
  -> refresh provider BUILD_READY queue
  -> ingest provider evidence
  -> aggregate opportunities
  -> refresh research reports
  -> refresh validation
  -> mark stale build proposals when upstream evidence changes
  -> advance approved sandbox/provider jobs
  -> run QC
  -> stop at human gates
```

This makes the system self-correcting in both directions.

## Invalidation rules

Examples:

- provider license changes -> invalidate affected compositions;
- provider becomes archived -> mark queue entry stale and re-evaluate dependent stacks;
- material evidence changes -> refresh research and validation;
- product definition changes -> stale the current build proposal;
- script changes -> invalidate storyboard, voices, animation, render, and QC;
- reusable character asset changes -> invalidate only scenes referencing that asset when possible;
- release artifact changes -> invalidate prior release review and live validation.

## v0.5 implementation order

### Step A — Close Step 4A acceptance recovery

Finish the existing Vercel sacrificial PREPARE recovery using provider GET-only reconciliation. Preserve exactly one provider PREPARE write and keep Production pointers unchanged.

### Step B — Provider Composition Planner v0.2

- role compatibility matrix;
- license compatibility;
- stack cost model;
- candidate composition persistence;
- evidence-backed side-business hypotheses;
- stale/recompute behavior.

### Step C — Production Provider Contract v0.1

Implemented as the common execution contract between Human Build Approval and provider-specific digital-asset production:

- provider definition registry and immutable per-job contract snapshots;
- generic provider job/stage state model;
- structured versioned manifest envelopes with SHA-256 identity;
- topological dependency graph and transitive downstream invalidation;
- bounded retry state machine;
- Product BUILD_READY/source-fingerprint reconciliation before stage execution;
- append-only resource and estimated-cost accounting;
- provider audit event stream;
- hard database guards keeping external side effects, Production execution, and publishing disabled.

See `docs/PRODUCTION_PROVIDER_CONTRACT_V01.md`.

### Step D — Shrimp Animation Provider v0.1

- animation job schema;
- scene manifest;
- reusable asset registries;
- TTS adapter;
- ComfyUI adapter;
- Remotion renderer;
- FFmpeg packager;
- QC.

### Step E — Scheduler and observability

- stage-level resumability;
- automatic retry policy;
- deterministic reruns;
- queue metrics;
- resource accounting;
- audit/event history;
- daily summary.

### Step F — Publish gate and adapters

- publication package;
- explicit human publish approval;
- platform-specific publisher adapters;
- no autonomous public posting in the baseline release.

## Non-goals

- bypassing human build/release/production gates;
- treating provider BUILD_READY as product BUILD_READY;
- automatic public publishing without an explicit human gate;
- copying proprietary character packs or other protected assets;
- cloning real people's voices without rights;
- requiring high-VRAM full-frame video generation for the baseline animation workflow.
