# Shrimp Animation Provider v0.1 — Step 10B.21

## External Verification Anchor + Proof Bundle Signing + Independent Auditor Verification + Tamper-Evident Export Registry

Step 10B.21 turns the Step 10B.20 audit proof into an independently verifiable
artifact. It does not publish media, change reliability policy, repair evidence,
or write to an external provider automatically.

## Trust model

1. The latest Step 10B.20 audit proof is summarized into a deterministic proof
   bundle snapshot.
2. The canonical bundle SHA-256 is signed with an Ed25519 private key supplied
   through `SHRIMP_BILIBILI_AUDIT_SIGNING_PRIVATE_KEY_PEM_B64`.
3. The bundle stores only the detached signature, public key and public-key
   fingerprint. The private key is never persisted in the database or returned
   by an API.
4. Any auditor can recompute the bundle SHA and verify the signature using only
   the exported bundle.
5. An external anchor receipt can be registered only when its digest exactly
   matches the signed bundle SHA-256.
6. Every export is appended to an immutable registry whose entries point to the
   previous export ID and previous export SHA-256.

## Persistence

Migration `062_shrimp_bilibili_external_verification.sql` adds:

- `shrimp_bilibili_audit_proof_bundles`
- `shrimp_bilibili_external_verification_anchors`
- `shrimp_bilibili_tamper_evident_export_registry`

All three evidence families are immutable through database triggers.

## API

Read-only verification:

- `GET /v1/shrimp-animation/bilibili-external-verification`
- `GET /v1/shrimp-animation/bilibili-audit-proof-bundles`
- `GET /v1/shrimp-animation/bilibili-audit-proof-bundles/{id}`
- `GET /v1/shrimp-animation/bilibili-audit-proof-bundles/{id}/verify`
- `GET /v1/shrimp-animation/bilibili-external-verification-anchors`
- `GET /v1/shrimp-animation/bilibili-audit-export-registry`
- `GET /v1/shrimp-animation/bilibili-audit-export-registry/verify`

Human-gated writes use `X-Shrimp-Reliability-Governance-Key`:

- `POST /v1/shrimp-animation/bilibili-audit-proof-bundles`
- `POST /v1/shrimp-animation/bilibili-audit-proof-bundles/{id}/anchors`
- `POST /v1/shrimp-animation/bilibili-audit-proof-bundles/{id}/export`

## External anchor boundary

The system does **not** contact a transparency log, timestamp authority,
blockchain, object store or other third party in Step 10B.21. An externally
obtained receipt/reference is imported and cryptographically bound to the
bundle. A later adapter may automate anchoring only after separate provider
authorization and live acceptance.

## Fail-closed rules

- Missing signing private key: bundle creation is rejected.
- Non-Ed25519 private key: bundle creation is rejected.
- Bundle SHA mismatch: verification fails.
- Detached signature failure: verification fails.
- Public-key fingerprint mismatch: verification fails.
- External anchor digest different from bundle digest: registration is rejected.
- Invalid bundle: export registry append is rejected.
- Export sequence, pointer or SHA-chain damage: registry verification fails.

## Acceptance target

The stage is accepted only when the full migration set reaches 062 and tests
prove:

`Audit Proof -> Ed25519 Bundle -> Independent Verify PASS -> External Anchor -> Export Registry -> Registry Verify PASS`

plus tamper rejection and wrong-anchor-digest rejection.

Inherited Step 10B.20A test-isolation failures remain tracked separately and
must not be hidden by this stage.
