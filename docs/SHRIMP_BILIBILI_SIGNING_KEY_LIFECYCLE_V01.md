# Shrimp Animation Provider v0.1 — Step 10B.22

## Signing Key Rotation + Revocation + Multi-Key Verification + Historical Proof Validity

Step 10B.22 replaces the earlier ad-hoc key lifecycle design with a GitHub-first
architecture based on three mature open-source projects:

- **theupdateframework/python-tuf** — Apache-2.0
  - versioned Root metadata
  - key IDs
  - role thresholds
  - add/revoke key semantics
  - root continuity model
- **secure-systems-lab/securesystemslib** — MIT
  - Ed25519 public-key representation
  - independent signature verification
- **openbao/openbao** — MPL-2.0
  - Transit signing
  - non-exported private-key custody
  - versioned asymmetric keys
  - controlled key rotation

No private OpenBao key material is exported into this application.

## Architecture

```
Proof Bundle
   │
   ▼
Signing Provider
   ├─ LOCAL_PEM          (compatibility / test / migration mode)
   └─ OPENBAO_TRANSIT    (target custody mode)
            │
            ├─ GET  /v1/transit/keys/:name
            ├─ POST /v1/transit/sign/:name
            └─ POST /v1/transit/keys/:name/rotate
   │
   ▼
Immutable Signing Key Registry
   │
   ▼
Versioned python-tuf Root Snapshot
   │
   ▼
securesystemslib historical verification
```

## Safety boundaries

- `OPENBAO_TRANSIT` is opt-in.
- OpenBao rotation is independently disabled by default.
- Rotation requires `X-Shrimp-Signing-Key-Rotation-Key`.
- The rotation key must be independent from release, publishing, live
  acceptance and reliability governance keys.
- A proof-bundle signing operation may perform at most one Transit sign write.
- A rotation operation may perform at most one Transit rotate write.
- Revoking the last key authorized by the current TUF root fails closed.
- No automatic provider or Production deployment write is introduced.
- Private keys are never persisted to PostgreSQL or returned by an API.

## Historical validity semantics

Normal retirement or a revocation whose `effective_at` is after a historical
bundle's signing time does not invalidate that bundle.

A revocation whose `effective_at` is at or before the signing time represents
a forensic determination that the key was already compromised. Bundles signed
at or after that effective time fail verification.

This keeps scheduled rotation from destroying historical proof validity while
still allowing retroactive compromise response.

## Trust Root model

Each immutable `shrimp_bilibili_signing_trust_roots` row contains a
python-tuf Root snapshot.

The system validates:

- sequential root versions
- previous-root pointers
- immutable root SHA-256
- TUF Root parse validity
- Root role key set
- Root role threshold
- satisfiable threshold

Old Root snapshots remain available so bundles signed by retired keys can still
be validated independently.

## API

Read-only:

- `GET /v1/shrimp-animation/bilibili-signing-key-lifecycle`
- `GET /v1/shrimp-animation/bilibili-signing-keys`
- `GET /v1/shrimp-animation/bilibili-signing-key-events`
- `GET /v1/shrimp-animation/bilibili-signing-trust-roots`
- `GET /v1/shrimp-animation/bilibili-signing-trust-roots/verify`
- `GET /v1/shrimp-animation/bilibili-signing-key-lifecycle/verify-all`
- `GET /v1/shrimp-animation/bilibili-audit-proof-bundles/{id}/verify-signing-trust`

Human-gated:

- `POST /v1/shrimp-animation/bilibili-signing-key-lifecycle/bootstrap`
- `POST /v1/shrimp-animation/bilibili-signing-key-lifecycle/rotate`
- `POST /v1/shrimp-animation/bilibili-signing-keys/{fingerprint}/revoke`

## Configuration

```
SHRIMP_BILIBILI_AUDIT_SIGNING_PROVIDER=LOCAL_PEM|OPENBAO_TRANSIT
SHRIMP_BILIBILI_OPENBAO_URL=
SHRIMP_BILIBILI_OPENBAO_TOKEN=
SHRIMP_BILIBILI_OPENBAO_TRANSIT_MOUNT=transit
SHRIMP_BILIBILI_OPENBAO_KEY_NAME=shrimp-bilibili-audit
SHRIMP_BILIBILI_OPENBAO_TIMEOUT_SECONDS=10
SHRIMP_BILIBILI_OPENBAO_ROTATION_ENABLED=false
SHRIMP_BILIBILI_TUF_ROOT_VALID_DAYS=365
SHRIMP_BILIBILI_TUF_ROOT_THRESHOLD=1
SHRIMP_BILIBILI_SIGNING_KEY_ROTATION_KEY=
```

## Acceptance target

```
Migration 001 -> 063
-> bootstrap current key
-> TUF Root v1 PASS
-> sign Proof Bundle
-> securesystemslib verify PASS
-> rotate key
-> TUF Root v2 PASS
-> old bundle still PASS
-> new bundle PASS
-> multi-key verify PASS
-> later revocation preserves historical bundle
-> retroactive compromise invalidates affected bundle
-> last-current-key revocation FAIL-CLOSED
-> OpenBao sign write <= 1 per bundle
-> OpenBao rotate write <= 1 per rotation
-> 0 unexpected Provider/Production writes
```
