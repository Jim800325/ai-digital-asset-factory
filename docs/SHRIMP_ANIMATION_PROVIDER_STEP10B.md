# Shrimp Animation Provider v0.1 — Step 10B

## Goal

Step 10B pivots sacrificial live publisher acceptance from YouTube to Bilibili without changing the Step 10 exactly-once execution engine.

The live path is private-only: PUBLISH_AUTHORIZED → BILIBILI_CONTROLLED snapshot → sacrificial MID preflight → UPOS upload → is_only_self=1 submit → metadata finalize → Creator Center read-back → VERIFIED_PRIVATE → one cleanup write → read-only deletion verification → CLEANED_UP.

## Authentication

Preview-only secrets:

    SHRIMP_BILIBILI_SESSDATA
    SHRIMP_BILIBILI_BILI_JCT
    SHRIMP_BILIBILI_DEDE_USER_ID
    SHRIMP_BILIBILI_DEDE_USER_ID_CKMD5   # optional

The authenticated MID must equal execution.account_reference and DedeUserID. Secrets are never returned by /health or readiness.

Independent Step 10B gate:

    SHRIMP_BILIBILI_LIVE_ACCEPTANCE_KEY
    X-Shrimp-Bilibili-Live-Acceptance-Key

## Private-only contract

Both initial submission and metadata finalize force is_only_self=1, no_disturbance=1, closed replies and closed danmaku. Read-back must confirm is_only_self=1 or acceptance fails closed.

## Upload and reconciliation

The adapter performs authenticated preupload probing, UPOS multipart upload, finalize, and creator-center archive submission. Every archive carries a deterministic marker derived from upload_idempotency_key. Lost submit responses are reconciled by bounded recent-archive scans; multiple matches fail closed.

## Publish phase

Step 10 Publish maps to metadata finalize on the exact AID while preserving is_only_self=1. Step 10 publish_write_count remains bounded to one.

## Cleanup

Cleanup has its own provider mutation budget:

    0 <= cleanup_write_count <= 1

If delete outcome is ambiguous, a second delete is forbidden. Only read-back verification may continue.

A successful acceptance requires CLEANED_UP plus private/read-back/cleanup verification, cleanup_write_count=1, production_account_touched=false, and public_visibility_observed=false.

## Audit

Migration 043 adds shrimp_animation_bilibili_live_acceptance_runs with immutable MID/AID/BVID/provider URL identity and monotonic verification evidence.

## API

Readiness:

    GET /v1/shrimp-animation/bilibili-live-acceptance/readiness

Run:

    POST /v1/shrimp-animation/publish-executions/{execution_id}/bilibili-live-acceptance
    X-Shrimp-Bilibili-Live-Acceptance-Key: <independent-key>

Read audit:

    GET /v1/shrimp-animation/publish-executions/{execution_id}/bilibili-live-acceptance

## Readiness

The readiness endpoint exposes only booleans, counts and blocker keys. It checks Preview isolation, Step 9/10 gates, BILIBILI_CONTROLLED adapter selection, live-acceptance gate, Cookie presence, sacrificial account/target allowlists, real/main deny lists, disjoint policy sets, and a runnable Bilibili execution.

## Default safety state

    SHRIMP_BILIBILI_LIVE_ACCEPTANCE_ENABLED=false
    SHRIMP_PUBLISH_EXECUTOR_ENABLED=false
    SHRIMP_PUBLISH_EXECUTION_ADAPTER=MOCK

Deployment alone cannot submit to Bilibili. Real acceptance requires Preview-only Cookie secrets, explicit sacrificial MID/target allowlisting, real/main MID/target denylisting, a current PUBLISH_AUTHORIZED plan, a BILIBILI_CONTROLLED execution and the independent Step 10B key.
