# Shrimp Animation Provider v0.1

## Goal

Build a resumable, mostly unattended 2D story-animation provider inside AI Digital Asset Factory.

The provider is inspired by the production pattern commonly used for lightweight serialized story animation: reusable character sprites, backgrounds, preset expressions/actions, deterministic camera movement, dialogue voice tracks, subtitles, sound effects, and programmatic rendering.

It is not coupled to any single creator, proprietary character pack, or closed animation application.

## Provider identity

```text
provider_id: shrimp_animation
asset_class: CONTENT_IP
provider_version: v0.1
execution_mode: SANDBOX_FIRST
external_publish: HUMAN_GATED
```

## Pipeline

```text
Story / brief
    |
    v
Story Planner
    |
    v
Script Planner
    |
    v
Scene Planner
    |
    v
scene_manifest.json
    |
    +------------------+
    |                  |
    v                  v
Asset Planner       Voice Planner
    |                  |
    v                  v
ComfyUI Adapter     TTS Adapter
    |                  |
    +--------+---------+
             |
             v
      Animation Planner
             |
             v
       Remotion Renderer
             |
             v
         FFmpeg Pack
             |
             v
          QC Gate
             |
             v
        PUBLISH_READY
             |
             v
    Human Publish Approval
```

## Core modules

Suggested package layout:

```text
app/providers/animation/
  __init__.py
  registry.py
  models.py
  states.py
  invalidation.py

app/providers/animation/shrimp/
  provider.py
  story_planner.py
  script_planner.py
  scene_planner.py
  character_registry.py
  background_registry.py
  action_registry.py
  camera_registry.py
  voice_adapter.py
  comfyui_adapter.py
  remotion_adapter.py
  ffmpeg_adapter.py
  qc.py
```

Renderer project:

```text
renderer/
  remotion/
    src/
      compositions/
      components/
      actions/
      camera/
      subtitles/
      audio/
```

## scene_manifest v0.1

Example:

```json
{
  "episode_id": "ep-0001",
  "fps": 30,
  "resolution": {"width": 1920, "height": 1080},
  "scenes": [
    {
      "scene_id": "s001",
      "duration_ms": 6200,
      "background_id": "palace_day",
      "camera": [
        {"type": "zoom", "from": 1.0, "to": 1.08, "start_ms": 0, "end_ms": 6200}
      ],
      "characters": [
        {
          "character_id": "hero",
          "asset_variant": "idle",
          "position": {"x": 0.34, "y": 0.72},
          "actions": [
            {"type": "enter", "direction": "left", "start_ms": 0, "duration_ms": 600},
            {"type": "talk", "start_ms": 900, "end_ms": 4100}
          ]
        }
      ],
      "dialogue": [
        {
          "speaker": "hero",
          "text": "陛下，我有一计。",
          "start_ms": 900,
          "end_ms": 4100,
          "voice_asset_id": "voice-s001-001"
        }
      ]
    }
  ]
}
```

Coordinates should be normalized where practical so the same scene can target multiple resolutions.

## Action registry v0.1

Baseline deterministic actions:

- idle
- talk
- walk_left
- walk_right
- enter
- exit
- jump
- shake
- nod
- bow
- turn
- scale_pulse
- hit_reaction
- surprised
- angry
- laugh

Camera primitives:

- static
- pan
- zoom
- push_in
- pull_out
- shake
- focus_left
- focus_right

Transitions:

- cut
- fade
- crossfade
- slide
- flash

These actions should be renderer primitives, not per-episode generated code.

## Character registry

Each reusable character pack should include:

```text
character_id
display_name
base_asset
expressions[]
action_variants[]
anchor_points
default_scale
voice_profile_id
license
source
sha256
```

A character asset can be reused across many episodes without re-running image generation.

## Voice layer

The provider interface should be vendor-neutral:

```text
synthesize(text, voice_profile, options) -> VoiceArtifact
```

Initial adapters may include:

- GPT-SoVITS-compatible local endpoint
- external TTS provider behind a configured adapter

The registry must store voice provenance and usage rights. Do not assume permission to clone a real person's voice.

## ComfyUI role

ComfyUI is an asset generator, not the primary timeline renderer.

Recommended use:

- transparent character PNG/WebP
- expressions
- props
- backgrounds
- cover images
- optional short motion inserts

Avoid using diffusion for every frame of every scene in v0.1.

## Remotion role

Remotion receives structured manifests and reusable assets, then renders:

- sprite placement
- transforms
- action timing
- camera movement
- subtitles
- dialogue timing
- sound effects
- compositing

The renderer should be deterministic for a given manifest and asset set.

## FFmpeg role

FFmpeg performs:

- final audio mix
- loudness normalization
- muxing
- encoding
- optional intro/outro packaging
- output verification

## QC gate

Minimum automated checks before `PUBLISH_READY`:

- all referenced assets exist
- all asset hashes match
- all voice files exist
- no timeline overlap outside allowed rules
- no negative/overflow timestamps
- video duration within target tolerance
- audio duration compatible with scene timing
- subtitles fit safe area
- no missing frames/render failures
- final file decodes successfully
- output SHA-256 captured
- asset/source provenance complete

## Scheduler behavior

Jobs should be restartable at the failed stage.

Example:

```text
ASSETS_READY
  -> VOICE generation fails
  -> VOICE_FAILED
  -> retry voice only
  -> VOICES_READY
  -> continue animation
```

Upstream edits invalidate downstream products:

```text
SCRIPT changed
  -> invalidate STORYBOARD
  -> invalidate VOICES
  -> invalidate ANIMATION
  -> invalidate RENDER
  -> invalidate QC
```

Asset regeneration should invalidate only scenes that reference the changed asset when possible.

## v0.1 acceptance target

A successful first acceptance run should prove:

1. one structured story becomes a valid script
2. script becomes a deterministic `scene_manifest`
3. at least two reusable characters and one background are loaded from registries
4. at least two voices are synthesized
5. Remotion renders a multi-scene animation
6. FFmpeg creates the final MP4
7. QC produces a passing report
8. job stops at `PUBLISH_READY`
9. no external publishing occurs automatically

## Later extensions

After v0.1 is stable:

- automated topic discovery
- episodic character memory
- multi-language dubbing
- cover/title generation
- Bilibili metadata package
- shorts/reels aspect-ratio variants
- optional Wan/LTX/Hunyuan generated insert shots
- analytics feedback into story scoring

The baseline provider should remain usable without those extensions.
