# Shrimp Animation Provider v0.1 — Implementation Step 4

## Purpose

Step 4 turns verified scene, image, and voice inputs into one deterministic animation timeline and a verified Remotion composition contract.

The stage boundary is:

```text
SCENE SUCCEEDED
ASSETS SUCCEEDED
VOICES SUCCEEDED
        |
        v
Animation Planner
        |
        v
Animation Timeline Manifest
        |
        v
Remotion Composition Adapter
        |
        v
canonical props + source hash verification
        |
        v
ANIMATION SUCCEEDED
        |
        v
RENDER PENDING
```

Step 4 does not render the final MP4.

## Migration 036

Migration 036 adds:

- `animation_manifest_sha256` to `shrimp_animation_jobs`;
- `remotion_props_sha256` to `shrimp_animation_jobs`;
- immutable `shrimp_animation_compositions` records.

Each composition records exact input lineage:

- Scene manifest SHA-256;
- Asset artifact manifest SHA-256;
- Voice artifact manifest SHA-256;
- Animation manifest SHA-256;
- Remotion props SHA-256;
- renderer key/version;
- renderer project source SHA-256;
- composition ID;
- props URI and canonical props snapshot.

Only one current composition is allowed per provider job.

## Deterministic frame conversion

Scene timing remains authored in milliseconds, but Remotion consumes integer frames.

Step 4 converts milliseconds with deterministic integer arithmetic:

```text
frames = floor((milliseconds * fps + 500) / 1000)
```

Scene frame ranges are contiguous and global:

```text
scene 1: 0 -> N
scene 2: N -> M
scene 3: M -> total
```

No wall-clock time or random number participates in timeline planning.

## Media binding

Every character action resolves to a verified character artifact using:

```text
character:{character_id}:{asset_variant}
```

Every background resolves through:

```text
background:{background_id}
```

Every dialogue line resolves to its verified `voice_asset_id`.

Missing verified media blocks ANIMATION rather than falling back to unverified files.

## Dialogue and subtitles

Each dialogue cue contains:

- line ID;
- speaker;
- verified voice media reference;
- frame start/end;
- measured voice duration.

Subtitle cues use the exact Script/Scene text and align to the dialogue/audio interval.

If measured audio would overflow its scene, timeline planning fails closed. Step 4 does not silently truncate speech.

## Camera timeline

Scene camera primitives from the deterministic Camera Registry become frame-bounded Remotion cues with from/to scale values.

The baseline renderer supports:

- static;
- pan;
- zoom;
- push_in;
- pull_out;
- shake;
- focus_left;
- focus_right.

Camera cues never escape their scene duration.

## Remotion project

The repository now contains a baseline renderer under:

```text
renderer/remotion/
  package.json
  tsconfig.json
  src/index.tsx
  src/Root.tsx
  src/ShrimpAnimation.tsx
```

It composes:

- background images;
- reusable character sprites;
- dialogue audio;
- subtitle overlays;
- frame-based camera transforms;
- scene sequences.

The renderer consumes only the Step 4 canonical props contract.

## Remotion adapter

`RemotionRendererAdapter.prepare()` writes canonical JSON props into an explicitly configured output root, rereads the bytes, verifies SHA-256, and hashes the renderer project source tree.

It also exposes a shell-free argument-list render command builder for the later RENDER stage.

Step 4 does not call that render command.

## Replay and invalidation

If ANIMATION is already SUCCEEDED, replay returns the current timeline manifest without calling the adapter again.

Any upstream Content Brief change causes the generic Provider Contract to stale downstream stages and Step 4 additionally stales the current composition record and clears current animation/props hashes.

## Runtime default

```text
SHRIMP_ANIMATION_ADAPTER=DISABLED
```

A real internal worker must explicitly configure the Remotion project, props output root, composition ID, entrypoint, and preinstalled Remotion CLI.

## CI acceptance

CI uses `FIXTURE_REMOTION`, not the real renderer.

The acceptance proves:

- three scenes become contiguous frame ranges;
- 45 seconds at 30 fps becomes 1350 total frames;
- all character/background references come from verified ASSETS;
- all six dialogue tracks come from verified VOICES;
- six subtitle cues are composed;
- camera cues stay inside scene boundaries;
- canonical Remotion props are SHA-256 verified;
- ANIMATION becomes SUCCEEDED;
- RENDER and QC remain PENDING;
- replay performs zero additional adapter calls;
- composition identity/content is database-immutable;
- upstream change stales the composition and downstream stages.

## Safety boundary

Step 4 performs no final video render, FFmpeg packaging, Production deployment, Production promotion/rollback, or external publishing.

Provider guards remain:

```text
external_side_effects = DENY
production_execution_enabled = false
publish_enabled = false
```
