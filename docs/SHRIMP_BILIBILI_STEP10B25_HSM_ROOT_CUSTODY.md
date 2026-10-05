# Shrimp Animation Provider v0.1 — Step 10B.25

## HSM/KMS-backed Root Custody + PKCS#11 External Key Provider + Root Ceremony Disaster Recovery

### GitHub-first references

This stage reuses mature open-source designs instead of inventing a new HSM protocol:

- `pyauth/python-pkcs11` (MIT)
  - high-level PKCS#11 API
  - Ed25519 / EC_EDWARDS + EDDSA support
  - SoftHSM2-backed integration tests upstream
- `SoftHSM/SoftHSMv2`
  - software PKCS#11 token for isolated CI acceptance
- `theupdateframework/python-tuf`
  - offline Root role
  - threshold root-signing model
  - root continuity and recovery semantics
- `openbao/openbao`
  - future external-key / KMS provider target
  - not treated as the only production baseline while its external-key surface is still evolving

## Private-key boundary

Private root-custody key material is never:

- inserted into PostgreSQL
- returned by an API
- included in the offline Root backup manifest
- exported by the application

The application stores only:

- PKCS#11 provider locator
- public key
- SHA-256 fingerprint
- token/key labels and non-secret key ID
- immutable ceremony / backup / restore audit evidence

Production HSM/KMS private-key backup must use the vendor-native protected backup mechanism.

## PKCS#11 acceptance

GitHub Actions installs SoftHSM2 and initializes a real token:

```
SoftHSM2 token
→ EC_EDWARDS / Ed25519 key generation
→ private key remains token-resident
→ EDDSA sign
→ exported public key verify
→ immutable HSM key registry
```

## Root ceremony

A ceremony binds:

- current TUF Root version
- current TUF Root SHA-256
- HSM key fingerprint
- participant fingerprints
- threshold
- explicit no-private-key-export statement
- zero Bilibili / Production write statement

The ceremony manifest is signed by the HSM key and independently verified using the exported public key.

## Offline Root backup

The backup contains:

- TUF Root snapshot
- Root SHA-256
- Root threshold
- authorized key fingerprints
- HSM public key
- HSM key locator metadata
- backup manifest SHA-256

It explicitly contains **no private key**.

## Disaster recovery drill

CI performs a real SoftHSM token-store recovery:

```
create token + key
→ sign challenge
→ copy token store to offline backup
→ remove active token store
→ verify key is unavailable
→ restore token store from backup
→ verify identical key fingerprint
→ sign new challenge
→ run Root Restore Drill
→ TUF Root chain PASS
→ HSM signature PASS
→ private-key export observed = false
```

## API

Read-only:

- `GET /v1/shrimp-animation/bilibili-hsm-root-custody`

Human-gated by independent `X-Shrimp-Root-Ceremony-Key`:

- `POST /v1/shrimp-animation/bilibili-hsm-root-custody/register`
- `POST /v1/shrimp-animation/bilibili-root-ceremonies`
- `POST /v1/shrimp-animation/bilibili-offline-root-backups`
- `POST /v1/shrimp-animation/bilibili-offline-root-backups/{backup_id}/restore-drill`

The Root Ceremony Key may not equal the signing rotation key, A/B transition approval keys,
reliability governance key, or publishing execution key.

## Migration

Migration 070 adds immutable records for:

- HSM key registry
- Root ceremonies
- offline Root backup manifests
- Root restore drills

Migrations 001–069 remain immutable.

## Acceptance target

```
Migration 001 → 070
→ python-pkcs11 install
→ SoftHSM2 token initialize
→ Ed25519 key generated inside token
→ HSM sign / public verify PASS
→ private key non-exportable at application boundary
→ Root Ceremony PASS
→ Offline Root Backup contains no private key
→ token store removed
→ key unavailable
→ token store restored
→ same fingerprint restored
→ post-restore sign PASS
→ TUF Root chain PASS
→ Restore Drill PASS
→ full pytest
→ 0 unintended Bilibili writes
→ 0 unintended Production writes
```


## CI execution note

The formal acceptance must run on the Pull Request workflow path, not a branch-push placeholder run. The PR workflow is the authoritative result for Migration 001→070, SoftHSM2 live custody acceptance, the complete pytest suite, and the production-disabled safety gate.
