# Shrimp Animation Provider v0.1 — Implementation Step 3

## Purpose

Step 3 turns Step 2 resource plans into verified reusable-image and dialogue-audio artifacts.

```text
SceneManifest
  -> ASSET / VOICE plans
  -> adapter execution
  -> bytes returned/resolved
  -> content signature verification
  -> SHA-256 recomputed
  -> provenance + license + rights verified
  -> immutable artifact rows
  -> immutable stage artifact manifest
  -> ASSETS SUCCEEDED / VOICES SUCCEEDED
```

A provider response, filename, HTTP status, or claimed checksum is never sufficient by itself.

## Migration 035

Migration 035 adds current asset/voice manifest hashes to shrimp_animation_jobs and the shrimp_animation_artifacts table. Artifacts store stage, logical key, kind, REUSED/GENERATED source mode, plan hash, URI, media type, byte size, recomputed SHA-256, license, APPROVED rights, adapter identity/version, request ID, provenance, measured audio duration, and verification state.

Artifact identity/content fields are immutable in PostgreSQL. Only one current artifact may exist for a job/stage/logical key.

## ComfyUI Asset Adapter

The adapter uses native ComfyUI-style endpoints:

```text
POST /prompt
GET  /history/{prompt_id}
GET  /view?...
```

A workflow JSON file is supplied by the internal worker and supports {{PROMPT}}, {{SEED}}, and {{OUTPUT_PREFIX}} placeholders. Seeds are deterministic from job ID + logical asset requirement + Asset Plan SHA-256.

Only GENERATION_REQUIRED requirements call ComfyUI. REUSE_READY requirements are reread from approved storage and verified against the registry hash.

## GPT-SoVITS Adapter

The initial compatible contract posts JSON to a configurable /tts path with exact text, language, reference audio, prompt text/language, WAV output, and non-streaming mode.

Voice synthesis is permitted only when the Voice Plan is READY_FOR_SYNTHESIS. Profiles must have APPROVED usage rights, known provenance/source type, a bound adapter, and required adapter metadata. CLONED_WITH_CONSENT requires explicit provenance rather than inference.

## Internal endpoint policy

ComfyUI and GPT-SoVITS adapters accept localhost, literal private/loopback/link-local addresses, or explicitly allowlisted hostnames. Arbitrary public hostnames fail closed unless explicitly allowlisted.

This is an internal worker contract, not a Vercel Preview execution path.

## Verification

Image verification checks non-empty bytes, media-type allowlist, PNG/JPEG/WebP signatures, claimed hash when present, recomputed SHA-256, license ID, APPROVED usage rights, and adapter provenance.

Voice verification checks non-empty WAV bytes, WAV decoding, positive measured duration, optional claimed-duration agreement, recomputed SHA-256, license, rights, and provenance.

## Resumability

Verified artifacts are cached by job + stage + logical key + resource-plan SHA-256. Partial failures can retry without regenerating already verified outputs. After a stage reaches SUCCEEDED, replay returns the current manifest without additional adapter calls.

## Stage completion

ASSETS reaches SUCCEEDED only after every asset requirement has one verified artifact. VOICES reaches SUCCEEDED only after every VoiceRequest has one verified WAV.

```text
ASSETS SUCCEEDED
VOICES SUCCEEDED
ANIMATION PENDING
```

Step 3 stops before animation/rendering.

## Runtime defaults

```text
SHRIMP_ASSET_ADAPTER=DISABLED
SHRIMP_VOICE_ADAPTER=DISABLED
```

Real internal-worker activation requires explicit endpoints, workflow path, provenance/license values, allowlisted hosts where needed, and allowed reusable-artifact storage roots.

## CI acceptance

CI uses explicit FIXTURE adapters and never labels their outputs as real ComfyUI or GPT-SoVITS artifacts. Acceptance covers four reused images, one generated missing background, six generated WAVs, 11 verified current artifacts, ASSETS/VOICES completion, ANIMATION remaining PENDING, zero extra adapter calls on replay, and PostgreSQL immutability enforcement.

## Safety boundary

Step 3 does not perform Remotion rendering, FFmpeg packaging, Vercel Production execution, Production promotion/rollback, or external publication. Generic provider-job flags remain external_side_effects=DENY, production_execution_enabled=false, publish_enabled=false.
