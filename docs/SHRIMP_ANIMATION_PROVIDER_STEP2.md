# Shrimp Animation Provider v0.1 — Implementation Step 2

## Purpose

Step 2 converts the deterministic SceneManifest into two immutable production-input plans without claiming that assets or voices already exist:

```text
SceneManifest
  -> Asset Planner
  -> ASSET resource plan

SceneManifest + ContentBrief
  -> Voice Planner
  -> VOICE resource plan
```

The generic Production Provider Contract stages `ASSETS` and `VOICES` remain `PENDING` or `STALE`. They are completed only by later adapters that produce verified artifacts.

## Migration 034

Migration 034 adds:

- `animation_reusable_assets`
- `animation_character_registry`
- `animation_character_variants`
- `animation_background_registry`
- `animation_action_registry`
- `animation_camera_registry`
- `animation_voice_profiles`
- `shrimp_animation_resource_plans`
- current ASSET / VOICE plan hashes on `shrimp_animation_jobs`

Action and camera primitives are seeded as deterministic renderer capabilities. Character, background and voice data are not seeded with fictional production rights.

## Reusable Asset Registry

Every reusable media asset records:

- stable asset key;
- asset kind;
- storage URI;
- media type;
- SHA-256;
- license identifier;
- provenance;
- usage-rights state;
- metadata.

Usage rights are explicit:

```text
APPROVED
REVIEW_REQUIRED
BLOCKED
```

The Asset Planner reuses only `APPROVED` assets.

## Character Registry

Each character can bind:

- base asset;
- reusable variants;
- default scale;
- anchor points;
- voice profile;
- metadata.

A requested `idle` variant may reuse the base asset. Other variants require an explicit reusable variant or become `GENERATION_REQUIRED`.

## Background Registry

A SceneManifest background ID resolves to a reusable approved background when available. Missing backgrounds become deterministic generation requirements for the later ComfyUI adapter.

## Action and Camera Registries

Step 2 seeds the renderer primitives already supported by SceneManifest.

Actions include `idle`, `talk`, movement, entrance/exit and reaction primitives.

Camera capabilities include `static`, `pan`, `zoom`, `push_in`, `pull_out`, `shake`, `focus_left` and `focus_right`.

Unknown or disabled capabilities make the Asset Plan `BLOCKED`; the planner does not invent episode-specific renderer code.

## Asset Plan

The deterministic Asset Planner records:

- character/variant requirements;
- background requirements;
- matched reusable assets;
- license/provenance snapshot;
- action capabilities;
- camera capabilities;
- reuse-ready count;
- generation-required count;
- rights-review count;
- blocked count;
- registry snapshot SHA-256.

States:

```text
REUSE_READY
GENERATION_REQUIRED
RIGHTS_REVIEW
BLOCKED
```

`GENERATION_REQUIRED` is not a failure: it is an explicit work item for the later asset-generation adapter.

## Voice Profile Registry

Step 2 stores voice planning metadata only. It does not store secrets or call a TTS service.

Each profile records:

- profile ID;
- adapter hint;
- language;
- source type;
- provenance;
- usage rights.

Voice source types include:

```text
SYNTHETIC
SELF_RECORDED
LICENSED
CLONED_WITH_CONSENT
UNKNOWN
```

A real-person clone is never inferred from a name or profile ID. Consent/rights must be represented explicitly before synthesis can be READY.

## Voice Plan Contract

Each dialogue line becomes one deterministic VoiceRequest containing:

- request SHA-256;
- scene and line IDs;
- speaker;
- exact text + text SHA-256;
- requested voice profile;
- timing window;
- adapter hint;
- provenance/source type;
- rights state.

Request states:

```text
READY_FOR_SYNTHESIS
PROFILE_REQUIRED
RIGHTS_REVIEW
ADAPTER_REQUIRED
BLOCKED
```

The whole Voice Plan is synthesis-ready only when every request is `READY_FOR_SYNTHESIS`.

## Immutable versioning

ASSET and VOICE plans are stored separately.

A plan identity includes its SceneManifest SHA-256 and the relevant registry snapshot SHA-256. Re-running with no changes returns the existing current version.

Registry changes only version the affected plan:

```text
new background asset
  -> ASSET v2
  -> VOICE remains v1

voice rights approved
  -> VOICE v2
  -> ASSET remains v2
```

Content Brief changes stale both current resource plans because the upstream story/script/scene lineage changed.

Plan payloads and identity fields are database-immutable.

## Safety boundary

Step 2 performs no:

- ComfyUI generation;
- TTS synthesis;
- Remotion rendering;
- FFmpeg packaging;
- Vercel Production execution;
- Production promotion/rollback;
- external publishing.

The provider job remains:

```text
external_side_effects = DENY
production_execution_enabled = false
publish_enabled = false
```

The next implementation step can consume these plans through ComfyUI and TTS adapters while preserving the generic retry, manifest and resource-accounting contract.
