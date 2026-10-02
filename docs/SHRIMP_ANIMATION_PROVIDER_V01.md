# Shrimp Animation Provider v0.1

## Goal

Build a resumable, mostly unattended 2D story-animation provider inside AI Digital Asset Factory.

The provider uses reusable character assets, backgrounds, preset expressions/actions, deterministic camera motion, dialogue voice tracks, subtitles, sound effects, and programmatic rendering. It is not tied to any single creator, proprietary character pack, or closed animation application.

## Provider identity

```text
provider_id: shrimp_animation
asset_class: CONTENT_IP
provider_version: v0.1
execution_mode: SANDBOX_FIRST
external_publish: HUMAN_GATED
```

## Implementation status

Step 1 is implemented on top of Production Provider Contract v0.1:

- provider registration;
- Migration 033 provider-job metadata;
- strict ContentBrief / Story / Script / Scene schemas;
- deterministic Story Planner;
- deterministic Script Planner;
- deterministic Scene Planner;
- immutable manifest persistence through the generic provider contract;
- transitive downstream invalidation when the Content Brief changes.

See `docs/SHRIMP_ANIMATION_PROVIDER_STEP1.md`.

Asset generation, TTS, Remotion rendering, FFmpeg packaging, and QC execution remain later steps.

## Step 2 implementation

Step 2 adds the reusable production-input layer without claiming that ASSETS or VOICES have been generated.

Implemented:

- reusable asset registry with SHA-256, provenance, license and usage-rights metadata;
- character registry with base assets, variants, anchors and voice-profile binding;
- background registry;
- deterministic action registry;
- deterministic camera registry;
- voice-profile rights registry;
- deterministic Asset Planner;
- deterministic Voice Planner Contract;
- immutable, versioned ASSET / VOICE resource plans;
- registry-snapshot SHA-256;
- resource-plan invalidation on upstream Content Brief changes.

Important state rule:

```text
resource plan created
!=
ASSETS stage completed
!=
VOICES stage completed
```

The generic `ASSETS` and `VOICES` stages remain `PENDING` or `STALE` until the later ComfyUI / TTS adapters actually produce verified artifacts.

See `docs/SHRIMP_ANIMATION_PROVIDER_STEP2.md`.

## Step 3 implementation

Step 3 turns the Step 2 ASSET / VOICE resource plans into verified stage artifacts.

Implemented:

- ComfyUI-native asset adapter contract and internal-endpoint implementation;
- GPT-SoVITS-compatible TTS adapter contract and internal-endpoint implementation;
- explicit private/allowlisted adapter endpoint validation;
- local reusable-artifact resolver constrained to configured storage roots;
- image signature + SHA-256 verification;
- WAV decoding + SHA-256 + measured duration verification;
- immutable artifact/provenance persistence;
- resumable artifact execution with cached verified outputs;
- ASSETS and VOICES generic Provider Contract stage completion only after verification;
- artifact manifest SHA-256 tracking on shrimp jobs;
- resource accounting for generated network bytes.

Default runtime remains fail-closed:

```text
SHRIMP_ASSET_ADAPTER=DISABLED
SHRIMP_VOICE_ADAPTER=DISABLED
```

CI uses explicit fake adapters only. Mock/fixture artifacts are never presented as real ComfyUI or GPT-SoVITS outputs.

See `docs/SHRIMP_ANIMATION_PROVIDER_STEP3.md`.

## Pipeline

```text
Content Brief
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

## Suggested module layout

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

renderer/remotion/
  src/
    compositions/
    components/
    actions/
    camera/
    subtitles/
    audio/
```

## scene_manifest v0.1

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
        {
          "type": "zoom",
          "from": 1.0,
          "to": 1.08,
          "start_ms": 0,
          "end_ms": 6200
        }
      ],
      "characters": [
        {
          "character_id": "hero",
          "asset_variant": "idle",
          "position": {"x": 0.34, "y": 0.72},
          "actions": [
            {
              "type": "enter",
              "direction": "left",
              "start_ms": 0,
              "duration_ms": 600
            },
            {
              "type": "talk",
              "start_ms": 900,
              "end_ms": 4100
            }
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

Coordinates should be normalized where practical so one manifest can target multiple output resolutions.

## Deterministic action registry

Baseline character actions:

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

These are renderer primitives, not episode-specific generated code.

## Character registry

Reusable character packs should store:

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

Approved character assets can be reused without re-running image generation.

## Voice layer

Provider-neutral contract:

```text
synthesize(text, voice_profile, options) -> VoiceArtifact
```

Initial adapters may include:

- GPT-SoVITS-compatible local endpoint;
- external TTS providers behind configured adapters.

Every voice profile must store provenance and usage rights. Permission to clone a real person's voice must never be assumed.

## ComfyUI role

ComfyUI is an asset generator, not the primary timeline renderer.

Recommended use:

- transparent character PNG/WebP;
- expression variants;
- props;
- backgrounds;
- cover images;
- optional short motion inserts.

Avoid diffusion-rendering every frame of every scene in the baseline provider.

## Remotion role

Remotion consumes structured manifests and reusable assets, then renders:

- sprite placement;
- transforms;
- action timing;
- camera movement;
- subtitles;
- dialogue timing;
- sound effects;
- compositing.

A fixed manifest plus fixed asset set should produce a reproducible render.

## FFmpeg role

FFmpeg performs:

- final audio mix;
- loudness normalization;
- muxing;
- encoding;
- optional intro/outro packaging;
- output verification.

## QC gate

Minimum automated checks before `PUBLISH_READY`:

- referenced assets exist;
- asset hashes match;
- voice files exist;
- no invalid timeline overlaps;
- no negative or overflow timestamps;
- video duration is within tolerance;
- audio duration is compatible with scene timing;
- subtitles fit safe areas;
- no missing frames or render failures;
- final file decodes successfully;
- final SHA-256 is captured;
- asset/source provenance is complete.

## Scheduler and resumability

Jobs resume at the failed stage.

```text
ASSETS_READY
  -> VOICE generation fails
  -> VOICE_FAILED
  -> retry voice only
  -> VOICES_READY
  -> continue animation
```

Upstream edits invalidate downstream artifacts.

```text
SCRIPT changed
  -> invalidate STORYBOARD
  -> invalidate VOICES
  -> invalidate ANIMATION
  -> invalidate RENDER
  -> invalidate QC
```

When possible, changing one reusable asset should invalidate only scenes that reference it.

## Integration with Project Flow v0.5

The provider is entered only after the normal opportunity/research/build gates have produced an approved `CONTENT_IP` build proposal.

The resulting rendered media remains an artifact under normal release review. Passing animation QC does not itself authorize external publication.

```text
Validated CONTENT_IP
  -> Human Build Approval
  -> shrimp_animation sandbox/provider execution
  -> QC_PASSED
  -> PUBLISH_READY
  -> release/package review
  -> Human Publish Approval
  -> publisher adapter
```

## v0.1 acceptance target

A successful first acceptance run proves:

1. one structured brief becomes a valid script;
2. the script becomes a deterministic `scene_manifest`;
3. at least two reusable characters and one background are loaded from registries;
4. at least two voices are synthesized;
5. Remotion renders a multi-scene animation;
6. FFmpeg creates the final MP4;
7. QC produces a passing report;
8. the job stops at `PUBLISH_READY`;
9. no external publishing occurs automatically.

## Later extensions

After v0.1 is stable:

- automated topic discovery;
- episodic character memory;
- multi-language dubbing;
- cover/title generation;
- Bilibili metadata package;
- shorts/reels aspect-ratio variants;
- optional generated insert shots;
- analytics feedback into story scoring.

The baseline provider should remain usable without those extensions.
