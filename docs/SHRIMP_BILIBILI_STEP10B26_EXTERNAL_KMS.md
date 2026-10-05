# Shrimp Animation Provider v0.1 — Step 10B.26

## Real External HSM/KMS Provider Registry + Provider Failover + Cross-KMS Root Ceremony

### GitHub-first references

This stage reuses official provider SDKs and existing trust standards:

- AWS SDK for Python / boto3
  - AWS KMS asymmetric sign/verify API
- Google Cloud KMS Python
  - asymmetric_sign + get_public_key
- Azure SDK for Python
  - Key Vault / Managed HSM CryptographyClient sign/verify
- OpenBao
  - external-key / Transit architecture
- python-tuf
  - current Root continuity remains authoritative

No provider credential is stored in PostgreSQL.

### Common cross-provider algorithm

Cross-KMS Ceremony uses:

```
ECDSA P-256 + SHA-256
```

The existing TUF/Proof chain remains Ed25519. Cloud KMS providers attest to the Root Ceremony manifest and do not rewrite historical Ed25519 proof semantics.

### Provider Registry

Migration 071 adds immutable records for:

- External KMS provider registrations
- provider health/failover events
- failover runs
- Cross-KMS Root Ceremonies

Provider registration stores only:

- provider type
- provider reference
- non-secret key locator
- public key / fingerprint when available
- priority
- signing algorithm

Credentials are resolved by the official provider SDK identity chain.

### Failover

Failover is disabled by default.

When explicitly enabled:

```
priority order
→ health check
→ sign digest
→ provider-specific verify
→ select first valid signer
→ immutable failover receipt
```

An unhealthy provider is skipped and recorded. No hidden provider switching occurs.

### Cross-KMS Root Ceremony

A ceremony passes only when:

- current TUF Root exists
- threshold >= 2
- enough distinct provider refs sign
- threshold is satisfied by distinct provider *types*
- every counted signature verifies through its provider adapter
- no private key export occurs
- Production and Bilibili writes remain zero

Two AWS keys cannot satisfy a 2-provider cross-KMS threshold by themselves.

### Provider-specific verification

- AWS KMS: remote KMS Verify
- GCP KMS: get_public_key + local ECDSA Prehashed(SHA-256) verification
- Azure Key Vault / Managed HSM: CryptographyClient Verify
- OpenBao External Key: Transit Verify

### Acceptance boundary

CI validates the official SDK request contracts without cloud credentials by injecting local P-256 test keys behind SDK-compatible clients.

This is a **provider contract acceptance**, not a live AWS/GCP/Azure account acceptance.

Real cloud-account writes/signatures remain disabled until a separate controlled live acceptance stage.

### API

Read-only:

- `GET /v1/shrimp-animation/bilibili-external-kms`

Human-gated by the existing independent Root Ceremony gate:

- `POST /v1/shrimp-animation/bilibili-external-kms/sync`
- `POST /v1/shrimp-animation/bilibili-cross-kms-root-ceremonies`

Both require `SHRIMP_BILIBILI_EXTERNAL_KMS_ENABLED=true`.

### Acceptance target

```
Migration 001 → 071
→ official AWS/GCP/Azure SDK install
→ AWS DIGEST + ECDSA_SHA_256 contract PASS
→ GCP asymmetric_sign + Prehashed local verify PASS
→ Azure ES256 sign/verify contract PASS
→ provider registry PASS
→ unhealthy provider skipped
→ healthy failover signer selected
→ Cross-KMS threshold >=2 PASS
→ same-provider-type threshold rejected
→ credentials_persisted=false
→ private_key_export_allowed=false
→ full pytest
→ production-disabled safety check
```
