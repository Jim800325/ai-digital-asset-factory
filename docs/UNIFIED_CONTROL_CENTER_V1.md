# Unified Control Center v1.0

## Scope

The root homepage is the read-only operating console for AI Digital Asset Factory / Shrimp Animation.

It unifies:

- System Status
- Production Pipeline
- Publishing
- Trust / Signing / DSSE / HSM / KMS
- Reliability Governance
- Operations / Needs Attention
- Step 10B.27A Real Cloud Acceptance status

## Design references

GitHub-first design direction:

- Tabler-style information density, status cards and responsive admin layout
- CoreUI-style sidebar navigation and operational grouping

The implementation remains dependency-free and reuses the existing server-rendered static surface so no new frontend framework is introduced.

## Safety

The homepage remains read-only.

It does not expose:

- Bilibili cookies
- signing private keys
- cloud credentials
- gate keys
- AWS/GCP/Azure secrets

It explicitly distinguishes:

- IMPLEMENTED
- INTEGRATION ACCEPTED
- REAL CLOUD LIVE ACCEPTED

Step 10B.27A remains WAITING until real immutable live-cloud acceptance evidence exists.

## Backend summary

`GET /v1/shrimp-animation/control-center/summary` includes:

- system
- pipeline
- review
- publishing
- bilibili
- trust_security
- governance
- release_track
- operations

Dashboard subsystem failures degrade to redacted issue codes rather than exposing secrets.

## Navigation

The v1 homepage links to existing dedicated workspaces rather than duplicating write actions:

- Human Review
- Publishing Authorization
- Accounts
- Jobs
- Quota
- Publisher Operations
- Reliability
- Governance Review
- Software Review
- API Console
