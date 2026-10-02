# Shrimp Animation Provider v0.1 — Implementation Step 1

## Scope

Step 1 binds `shrimp_animation` to Production Provider Contract v0.1 and implements the deterministic planning front half:

```text
CONTENT_BRIEF
  -> STORY
  -> SCRIPT
  -> SCENE
  -> ASSETS      (next step)
  -> VOICES      (next step)
  -> ANIMATION   (later)
  -> RENDER      (later)
  -> QC          (later)
```

No image generation, TTS, rendering, Production deployment, or external publishing is performed in Step 1.

## Provider registration

```text
provider_key: shrimp_animation
asset_class: CONTENT_IP
provider_version: v0.1
contract_version: v0.1
execution_mode: SANDBOX_FIRST
external_publish_mode: HUMAN_GATED
```

The provider is registered at application startup after migrations are current. Registration is deterministic and idempotent.

## Migration 033

`shrimp_animation_jobs` adds provider-specific planning metadata without duplicating the generic contract state machine.

It stores:

- immutable provider-job binding;
- episode ID;
- schema/planning versions;
- deterministic seed;
- current brief/story/script/scene SHA-256 values.

A database trigger rejects any row that is not bound to the `shrimp_animation` CONTENT_IP provider.

## Schemas

Step 1 adds Pydantic schemas for:

- `ContentBrief`
- `StoryManifest`
- `ScriptManifest`
- `SceneManifest`

All models use `extra="forbid"`.

The scene schema validates timeline boundaries and uses normalized character coordinates. The provider manifest envelope from the generic Production Provider Contract remains the persistence format and immutable version history.

## Deterministic adapters

### Story Planner

`plan_story(ContentBrief) -> StoryManifest`

The same validated brief always generates the same story beats and brief hash.

### Script Planner

`plan_script(ContentBrief, StoryManifest) -> ScriptManifest`

It rejects a Story manifest that does not match the current Content Brief.

### Scene Planner

`plan_scenes(ContentBrief, ScriptManifest) -> SceneManifest`

It deterministically allocates scene duration, dialogue windows, reusable character actions, backgrounds, transitions, and camera primitives.

No random number generator, current time, external API, or model call participates in these adapters.

## Invalidation

Updating a Content Brief uses the generic contract dependency graph:

```text
CONTENT_BRIEF changed
  -> STORY STALE
  -> SCRIPT STALE
  -> SCENE STALE
  -> ASSETS STALE
  -> VOICES STALE
  -> ANIMATION STALE
  -> RENDER STALE
  -> QC STALE
```

Re-running planning writes new immutable manifest versions and new SHA-256 values.

## Safety boundary

Step 1 preserves:

```text
external_side_effects = DENY
production_execution_enabled = false
publish_enabled = false
```

It does not invoke ComfyUI, TTS, Remotion, FFmpeg, Vercel Production execution, promotion, rollback, or a publishing adapter.
