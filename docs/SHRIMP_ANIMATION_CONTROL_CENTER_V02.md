# Shrimp Animation Control Center v0.2

## New administration pages

- `/animation/accounts` — Bilibili account and publish-target management
- `/animation/jobs` — animation pipeline jobs
- `/animation/executions` — controlled publisher executions and exactly-once budgets
- `/animation/settings` — runtime, publisher, Bilibili and security configuration overview

## Bilibili account model

The browser may manage non-secret metadata such as display name, target key,
account reference / MID and default category TID. Creating a target still requires
the independent Step 9 publish authorization key.

Bilibili cookies and gate keys remain server-side environment variables. The UI
only receives boolean readiness/configuration state and never receives secret
values. The Step 9 key entered in the account page is used only for the current
request and is explicitly cleared after the request. It is not written to browser
storage.

## Safety invariants

- sacrificial account allowlist remains required
- real account denylist remains required
- sacrificial target allowlist remains required
- real target denylist remains required
- allowlist and denylist must be disjoint
- Upload / Publish stay behind Step 10 / Step 10B
- homepage remains read-only
