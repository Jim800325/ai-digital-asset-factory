# Step 10B.4 — Bilibili Multi-Account Credential Binding + Account Health Check

## Purpose

Step 10B.4 separates non-secret Bilibili Account Profiles from credential
material and adds read-only account health verification.

## Credential slots

Each account may bind one credential slot:

- slot_key
- account_id
- environment-variable prefix
- ACTIVE / INACTIVE state
- credential/login/MID/publish-permission health state
- provider MID, display name and level
- last check/success/failure timestamps
- SHA-256 evidence only

The database never stores SESSDATA, bili_jct, DedeUserID__ckMd5, publish keys,
or live-acceptance keys.

For a slot with prefix:

    SHRIMP_BILIBILI_SLOT_TEST01

the runtime resolves only server-side:

    SHRIMP_BILIBILI_SLOT_TEST01_SESSDATA
    SHRIMP_BILIBILI_SLOT_TEST01_BILI_JCT
    SHRIMP_BILIBILI_SLOT_TEST01_DEDE_USER_ID
    SHRIMP_BILIBILI_SLOT_TEST01_DEDE_USER_ID_CKMD5

Only boolean presence information is returned to the browser.

## Health check

A health check is read-only. It performs:

1. Bilibili nav/login probe
2. authenticated MID read-back
3. expected MID comparison
4. DedeUserID/MID consistency check
5. read-only preupload capability probe

It performs no upload, publish, edit, or delete write.

Persisted outcomes include:

- CONFIGURED / MISSING / EXPIRED
- LOGGED_IN / LOGGED_OUT / ERROR
- MATCH / MISMATCH / ERROR
- ALLOWED / DENIED / ERROR
- HEALTHY / DEGRADED / UNHEALTHY

Health evidence has a configurable freshness limit:

    SHRIMP_BILIBILI_HEALTH_MAX_AGE_MINUTES=360

Stale health evidence cannot be automatically selected and cannot be frozen
into a new managed Publish Plan.

## Automatic sacrificial account selection

The selector only returns an account when all conditions hold:

- account ACTIVE
- credential slot ACTIVE
- health status HEALTHY
- health check still fresh
- account safety mode SACRIFICIAL
- global Step 10 account allowlist requirement satisfied

REAL accounts are never auto-selected for sacrificial acceptance.

## Publish Plan binding

When a managed Bilibili account has a credential slot, Step 9 freezes:

- account_profile_id / account_profile_sha256
- credential_slot_id / credential_slot_sha256
- credential slot health snapshot

A new plan is rejected if the slot is inactive, unhealthy, MID-mismatched,
permission-denied, or health evidence is stale.

Changing relevant slot health/binding state stales plans created from the older
credential-slot snapshot.

## Live Acceptance binding

Step 10B resolves the credential slot from the immutable Publish Plan and
constructs the Bilibili adapter with that slot's credentials. Legacy plans
without credential slots continue to use the earlier global credential
variables for backward compatibility.

The provider MID is resolved independently from internal account_key values, so
a Publish Target may use an account key while provider identity is still
verified against the actual numeric Bilibili MID.

## APIs

- POST /v1/shrimp-animation/bilibili-credential-slots
- GET /v1/shrimp-animation/bilibili-credential-slots
- GET /v1/shrimp-animation/bilibili-credential-slots/{slot_key}
- PATCH /v1/shrimp-animation/bilibili-credential-slots/{slot_key}
- POST /v1/shrimp-animation/bilibili-credential-slots/{slot_key}/health-check
- GET /v1/shrimp-animation/bilibili-credential-slots/{slot_key}/health-checks
- GET /v1/shrimp-animation/bilibili-account-selection/healthy

Slot mutations require the independent Step 9 publish key.
Health probes require the independent Step 10B live-acceptance key.
