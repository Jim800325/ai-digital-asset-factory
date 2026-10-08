# Step 10B.22A — Sacrificial Pipeline Fixture + Full UI Acceptance

Date: 2026-10-08  
Branch: `feature/unified-control-center-v1-pipeline-publishing`  
Fixture job: `57cf47e5-df9b-4470-be90-7748f76b4afe`  
Fixture episode: `shrimp-preview-10b22a`

## Result

**PASSED**

The isolated Preview database contains one sacrificial Shrimp Animation fixture generated through the real provider state machine. No real publishing provider was invoked.

## Pipeline state

All ten provider stages completed successfully:

1. CONTENT_BRIEF — SUCCEEDED
2. STORY — SUCCEEDED
3. SCRIPT — SUCCEEDED
4. SCENE — SUCCEEDED
5. ASSETS — SUCCEEDED
6. VOICES — SUCCEEDED
7. ANIMATION — SUCCEEDED
8. RENDER — SUCCEEDED
9. QC — SUCCEEDED
10. PACKAGE — SUCCEEDED

Final job state: `QC_PASSED`  
Human review state: `RELEASE_APPROVED`

## Media / QC evidence

The Preview Function runtime does not contain ffmpeg/ffprobe, so render/QC was performed in a disposable Vercel Sandbox using the repository's unchanged `shrimp-qc-v0.1-deterministic` analyzer.

- Resolution: 1920×1080
- FPS: 30
- Frames: 300
- Duration: 10,000 ms
- MP4 byte size: 827,296
- Render SHA-256: `157426e27042147c5dc60f23f8980c1477adc57afa5cf9fd6de48a8be3d3fbf6`
- QC result: PASSED
- Hard failures: 0
- QC report SHA-256: `b1c79cb48e09e8680e5efddb7251e32ff1ad3e88d7947b24e09da455530f9083`

Normal Production Render/QC behavior was not weakened or bypassed. The Sandbox attestation was accepted only by the temporary Preview-only acceptance harness, which was removed after acceptance.

## Publishing evidence

Target: `preview-10b22a-bilibili-mock`

Dry-run checks all passed, including:

- release approved
- review decision current
- episode bundle hash bound
- review package hash bound
- target active
- target execution disabled
- external publish disabled
- metadata contract valid
- network request count = 0
- credential access count = 0
- external write count = 0

Plan state: `PUBLISH_AUTHORIZED`  
Execution state: `SNAPSHOT_CREATED`  
Execution adapter: `MOCK`  
Upload write count: 0  
Publish write count: 0  
External side effects: `DENY`  
Production execution enabled: false  
Provider publish flag: false

## Pipeline Explorer evidence

The persisted read-only pipeline API returned:

- 1 fixture job
- 2 CURRENT resource plans (ASSET / VOICE)
- 11 current artifacts
- 1 current animation composition
- RELEASE_APPROVED review decision
- 1 PUBLISH_AUTHORIZED plan
- 1 MOCK SNAPSHOT_CREATED execution
- 14 non-empty SHA/provenance fields
- secrets_redacted = true
- console_write_actions = false

## Browser UI acceptance

The actual Preview HTML, JS, CSS and API responses were exercised with headless Chromium through a local read-only authentication proxy.

Desktop viewport: 1440×1000

- 10/10 stage cards rendered SUCCEEDED
- QC_PASSED visible
- RELEASE_APPROVED visible
- PUBLISH_AUTHORIZED visible
- SNAPSHOT_CREATED visible
- 14 SHA cards rendered
- no body horizontal overflow
- no JavaScript console errors

Mobile viewport: 390×844

- 10/10 stage cards rendered SUCCEEDED
- QC_PASSED visible
- RELEASE_APPROVED visible
- PUBLISH_AUTHORIZED visible
- SNAPSHOT_CREATED visible
- 14 SHA cards rendered
- no body horizontal overflow
- no JavaScript console errors

## Safety / cleanup

- Production was not promoted or modified.
- No real Bilibili upload/publish operation was attempted.
- No Cloud KMS execution was triggered.
- The disposable acceptance harness was removed from the branch after the fixture was created and verified.
- The temporary Preview fixture key is retired after acceptance.
- Fixture database evidence remains available for the read-only Pipeline Explorer acceptance record.
