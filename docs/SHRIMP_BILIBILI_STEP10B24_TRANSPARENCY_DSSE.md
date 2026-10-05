# Step 10B.24 — Rekor-compatible Transparency + Sigstore Trusted Time + in-toto DSSE + Offline Verifier

## GitHub-first reference stack

This stage reuses mature upstream designs instead of inventing new cryptographic formats:

- **in-toto/attestation v1.2.0**
  - Statement v1
  - DSSE envelope media type
- **secure-systems-lab/securesystemslib v1.5.1**
  - DSSE PAE
  - detached signature verification
  - unique-key signature threshold verification
- **sigstore/sigstore-python v4.5.0**
  - Rekor TransparencyLogEntry verification path
  - trusted-time semantics
  - RFC3161 verification dependencies
- **sigstore/timestamp-authority**
  - real RFC3161 Timestamp Authority
  - CI runs the official upstream dev server
- **sigstore/rekor**
  - intoto v0.0.2 proposed-entry format
  - inclusion proof + signed checkpoint + SET receipt model

## Data flow

```
TUF Root
→ in-toto Statement
→ DSSE PAE
→ detached Ed25519 signature(s)
→ securesystemslib threshold verification
→ Sigstore RFC3161 timestamp
→ optional Rekor intoto v0.0.2 append
→ persist receipt + Rekor public key
→ export self-contained offline bundle
→ verify without DB, network, or private key
```

## Important safety boundaries

- Rekor writes are opt-in: `SHRIMP_BILIBILI_REKOR_ENABLED=false` by default.
- TSA writes are opt-in: `SHRIMP_BILIBILI_TSA_ENABLED=false` by default.
- Both external operations remain behind the existing reliability governance gate.
- No Bilibili provider write is introduced.
- No Production deployment write is introduced.
- No private key is exported into an offline bundle.
- If DSSE threshold is not met, timestamp / transparency / offline export fail closed.

## Trusted time

The verifier follows the Sigstore model: at least one cryptographically verified time source is required.

Accepted sources:

1. RFC3161 Timestamp Authority response verified against its stored certificate chain.
2. Rekor integrated time only when the associated Rekor receipt, inclusion proof, checkpoint and SET verify.

An unverified wall-clock timestamp is never accepted as trusted time.

## Rekor compatibility

The adapter uses Rekor's `intoto v0.0.2` proposed-entry shape:

- complete DSSE envelope
- one public key per signature
- envelope SHA-256
- payload SHA-256

The receipt stores:

- entry UUID
- log index
- tree size
- root hash
- inclusion hashes
- signed checkpoint
- integrated time
- canonicalized body
- Rekor public key

Offline verification delegates Merkle/checkpoint/SET verification to
`sigstore.models.TransparencyLogEntry`, so the application does not implement
Rekor's tree verification algorithms.

## Offline bundle

The exported JSON contains everything required for verification:

- in-toto statement
- DSSE envelope
- detached signatures and public keys
- signature threshold
- trusted timestamp responses and chains
- Rekor receipts and public keys

`verify_exported_bundle_snapshot()` requires:

- no database
- no network
- no private key

## Migration discipline

- 067: attestation / trusted time / transparency / offline bundle records
- 068: detached threshold signatures
- 069: Rekor verifier public key

Earlier migrations remain immutable.

## Acceptance target

```
Migration 001 → 069
→ in-toto Statement
→ DSSE threshold PASS
→ official Sigstore TSA live timestamp
→ RFC3161 verification PASS
→ offline export PASS
→ database-free verification PASS
→ tampered payload FAIL
→ Rekor adapter uses official intoto v0.0.2 shape
→ sigstore-python verification path is used for Rekor receipts
→ 0 unintended Bilibili writes
→ 0 unintended Production writes
```


## Built-in Rekor-compatible baseline

External Rekor remains optional, but Step 10B.24 now has a mandatory,
network-independent transparency baseline based on the same primitives used by
Rekor:

- RFC6962 leaf hashing: `SHA256(0x00 || leaf)`
- RFC6962 node hashing: `SHA256(0x01 || left || right)`
- inclusion proof bound to `logIndex`, `treeSize`, and `rootHash`
- signed-entry-timestamp style receipt binding the DSSE body SHA, integrated
  time, index, tree size, and Merkle root
- signed checkpoint binding origin, tree size, and root hash

A transparency integrated time is trusted only when the receipt SET, Merkle
inclusion proof, and checkpoint all verify.

The local baseline uses `provider=REKOR_COMPATIBLE`. The external
`sigstore/rekor` adapter remains available behind its explicit opt-in flag.

## Standalone verifier

`scripts/verify_shrimp_offline_bundle.py` consumes an exported bundle JSON and
returns exit code 0 only when verification passes.

It requires:

- no PostgreSQL connection
- no network access
- no private key
- no application write permission

The underlying verifier module does not import the database layer.

Example:

```bash
python scripts/verify_shrimp_offline_bundle.py proof-bundle.json \\
  --trusted-root-sha256 <known-good-root-sha256>
```

## Final acceptance additions

The mandatory suite also verifies:

```
two-leaf RFC6962 tree
→ inclusion proof PASS
→ signed SET PASS
→ signed checkpoint PASS
→ integrated trusted time PASS
→ database-free verifier PASS
→ tampered Merkle proof FAIL
→ tampered checkpoint FAIL
→ tampered SET FAIL
```


The exported bundle includes the corresponding TUF Root snapshot for internal
consistency checks. For a true external trust decision, the verifier SHOULD
supply a previously trusted Root SHA-256 with `--trusted-root-sha256`.
The verifier then rejects a bundle whose embedded Root does not match that pin.
