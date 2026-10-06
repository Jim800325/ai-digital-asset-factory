# Shrimp Animation Provider v0.1 — Step 10B.27

## Controlled Live Cloud KMS Acceptance + Sacrificial Keys + Cross-Cloud Ceremony + Outage/Failover + Cleanup Verification

### GitHub-first references

This stage reuses official lifecycle patterns from:

- `awsdocs/aws-doc-sdk-examples`
  - KMS create/sign/verify/disable/schedule deletion
- `GoogleCloudPlatform/python-docs-samples`
  - Cloud KMS asymmetric signing key creation
  - key-version state transitions
  - disable/destroy lifecycle
- `Azure/azure-sdk-for-python`
  - KeyClient create EC key
  - CryptographyClient sign/verify
  - update_key_properties(enabled=False)
  - delete / recover / purge lifecycle
- Step 10B.26 official SDK adapters
  - boto3
  - google-cloud-kms
  - azure-keyvault-keys / azure-identity

No custom cloud KMS protocol is introduced.

## Live boundary

Real cloud writes are fail-closed unless both are true:

- `SHRIMP_BILIBILI_LIVE_CLOUD_KMS_ACCEPTANCE_ENABLED=true`
- `SHRIMP_BILIBILI_LIVE_CLOUD_KMS_CLEANUP_ENABLED=true`

All generated names must begin with:

```
SHRIMP_BILIBILI_LIVE_CLOUD_KMS_ALLOWED_NAME_PREFIX
```

Default:

```
shrimp-sacrificial-
```

Production-looking names outside the prefix are rejected before provider creation.

## Authentication

Credentials are not stored in PostgreSQL or API payloads.

Official SDK default identity chains are used:

- AWS IAM / workload role
- Google ADC / Workload Identity
- Azure DefaultAzureCredential / Managed Identity

## Provider lifecycle

### AWS KMS

```
create_key(
  KeyUsage=SIGN_VERIFY,
  KeySpec=ECC_NIST_P256
)
→ sign DIGEST / ECDSA_SHA_256
→ verify
→ disable_key
→ schedule_key_deletion(PendingWindowInDays=7)
→ describe_key read-back
→ signing must fail
```

### GCP Cloud KMS

An existing sacrificial KeyRing is required because KeyRings are not deleted.

```
create_crypto_key(
  ASYMMETRIC_SIGN,
  EC_SIGN_P256_SHA256
)
→ wait for version ENABLED
→ asymmetric_sign
→ public-key verification
→ set CryptoKeyVersion state DISABLED
→ read-back DISABLED
→ signing must fail
```

### Azure Key Vault / Managed HSM

```
create_ec_key(curve=P-256)
→ CryptographyClient sign/verify
→ update_key_properties(enabled=False)
→ read-back disabled
→ signing must fail
```

Deletion/purge may be performed by the operator after the acceptance window according to vault retention policy.

## Single-provider acceptance

A provider acceptance is PASS only when all of the following are true:

1. sacrificial key was created inside the allowlist
2. live provider signature verified
3. cleanup was requested
4. cleanup state was read back from the provider
5. post-cleanup signing was rejected
6. no credentials were persisted
7. Production writes = 0
8. Bilibili writes = 0

## Cross-cloud ceremony

Sequence:

```
create >=2 distinct cloud-provider keys
→ sign one TUF-root-bound ceremony manifest
→ verify every provider signature
→ satisfy provider threshold
→ disable primary provider key
→ primary signing must fail
→ fallback provider signs successfully
→ verify fallback signature
→ cleanup every sacrificial key
→ provider read-back confirms disabled
→ post-cleanup signing must fail for every key
→ only then persist ceremony_status=PASSED
```

A PASS row is never written before cleanup verification finishes.

## Outage drill

The first provider is treated as primary and the second as fallback.

Acceptance requires:

- primary disable succeeds
- primary signing fails
- fallback signing succeeds
- fallback signature verifies

The result is stored in an immutable outage-drill record.

## CLI

Single provider:

```bash
python scripts/run_shrimp_live_cloud_kms_acceptance.py \
  --provider AWS_KMS
```

Cross cloud:

```bash
python scripts/run_shrimp_live_cloud_kms_acceptance.py \
  --cross-cloud AWS_KMS GCP_KMS AZURE_KEY_VAULT \
  --threshold 2
```

## API

Read-only:

- `GET /v1/shrimp-animation/bilibili-live-cloud-kms`

Human-gated by `X-Shrimp-Root-Ceremony-Key`:

- `POST /v1/shrimp-animation/bilibili-live-cloud-kms/accept`
- `POST /v1/shrimp-animation/bilibili-live-cloud-kms/cross-cloud`

## Migration 072

Immutable tables:

- live provider acceptance runs
- outage/failover drills
- live cross-cloud ceremonies

Migrations 001–071 remain immutable.

## CI acceptance boundary

CI uses injected provider-compatible lifecycles and real P-256 cryptography to test:

- fail-closed live flags
- sacrificial allowlist
- sign/verify
- outage failure
- fallback verification
- cleanup read-back
- post-cleanup sign rejection
- cleanup idempotency
- no credential persistence
- zero Production/Bilibili writes

This does **not** claim real AWS/GCP/Azure account acceptance.

Real live status becomes true only after immutable successful live acceptance records exist.

## Final live acceptance target

```
Migration 001 → 072
→ real AWS sacrificial key acceptance
→ real GCP sacrificial key acceptance
→ real Azure sacrificial key acceptance
→ real cross-cloud 2-of-3 ceremony
→ primary outage simulation
→ fallback live signature PASS
→ every sacrificial key disabled/read back
→ every post-cleanup sign rejected
→ 0 credentials persisted
→ 0 Bilibili writes
→ 0 Production writes
```
