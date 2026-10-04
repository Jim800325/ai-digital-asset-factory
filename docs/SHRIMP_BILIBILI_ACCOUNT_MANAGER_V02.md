# Step 10B.3 — Bilibili Account Manager v0.2

## Account Registry

A Bilibili account is now a first-class profile instead of only an
`account_reference` string on a Publish Target.

Each profile stores non-secret operational metadata:

- account key, display name and MID
- ACTIVE / INACTIVE state
- account labels
- default category TID
- default copyright type
- default description and tags
- cover policy
- daily publish limit
- local publish time window and IANA timezone
- account safety mode: SACRIFICIAL or REAL

No Cookie or authorization key is stored in the registry.

## Plan integration

When a Bilibili Publish Target resolves to a registered account, Step 9:

1. checks the account is ACTIVE;
2. enforces account safety policy;
3. checks global allowlist / denylist consistency;
4. checks the account publish window;
5. checks the daily published count;
6. applies default description, tags and category;
7. enforces the cover requirement and PUBLIC visibility policy;
8. freezes an account-profile snapshot and SHA-256 into the immutable plan.

Changing an account profile stales plans that were built from the older profile.

Legacy Bilibili targets without a registered profile remain readable and
compatible so the upgrade does not invalidate earlier Step 9/10 acceptance
fixtures. The Control Center marks those bindings as UNREGISTERED.

## APIs

- `POST /v1/shrimp-animation/bilibili-accounts`
- `GET /v1/shrimp-animation/bilibili-accounts`
- `GET /v1/shrimp-animation/bilibili-accounts/{account_key}`
- `PATCH /v1/shrimp-animation/bilibili-accounts/{account_key}`

All mutations require the independent `X-Shrimp-Publish-Key`.

## Safety

- SACRIFICIAL + require_global_allowlist requires the profile key/MID to be in
  the Step 10 global account allowlist.
- REAL accounts must not be in the sacrificial allowlist and must be protected
  by the global account denylist.
- SESSDATA, bili_jct, DedeUserID_CKMD5, publish keys and execution keys remain
  server-side environment variables and are never returned by these APIs.
