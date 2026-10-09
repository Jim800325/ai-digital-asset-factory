# Step 10B.27A — Real Cloud Account Execution v2

This branch rebuilds Step 10B.27A from the certified Production `main` baseline instead of reviving the divergent historical branch.

## Scope

The acceptance target is at least two real cloud KMS providers. The initial pair is AWS KMS + Google Cloud KMS.

Required ceremony:

`Create → Sign → Verify → Cleanup/Disable → Read-Back → Sign-Blocked`

Outage/failover is deliberately reported as `PENDING_NEXT_GATE` until both single-provider live acceptances have completed cleanly. It must not be inferred from a single-provider result.

## Hard safety boundaries

- Vercel Preview only.
- Production runtime is always blocked.
- Default settings are fail-closed.
- No static AWS/GCP credentials are required or persisted.
- Vercel OIDC must identify the expected project, owner, and Preview environment.
- At least two providers must be selected and configured.
- A dedicated `REAL_CLOUD_EXECUTION_KEY` is required in addition to OIDC.
- Resource names must start with `REAL_CLOUD_ALLOWED_NAME_PREFIX`.
- AWS cleanup disables the key and schedules deletion, then reads state back.
- GCP cleanup disables the CryptoKeyVersion, then reads state back.
- A post-cleanup signing attempt must fail.
- Provider resource identifiers are hashed before being returned in acceptance evidence.
- No Bilibili writes and no Production writes are part of this stage.

## Environment contract

All variables belong to Preview only:

- `REAL_CLOUD_EXECUTION_ENABLED=true`
- `REAL_CLOUD_CLEANUP_ENABLED=true`
- `REAL_CLOUD_EXECUTION_KEY=<secret>`
- `REAL_CLOUD_ALLOWED_NAME_PREFIX=shrimp-sacrificial-`
- `REAL_CLOUD_SELECTED_PROVIDERS=AWS_KMS,GCP_KMS`
- `REAL_CLOUD_AWS_ROLE_ARN=<OIDC assumable role>`
- `REAL_CLOUD_AWS_REGION=<region>`
- `REAL_CLOUD_GCP_WORKLOAD_IDENTITY_AUDIENCE=<WIF audience>`
- `REAL_CLOUD_GCP_SERVICE_ACCOUNT=<optional impersonation target>`
- `REAL_CLOUD_GCP_PROJECT_ID=<sacrificial project>`
- `REAL_CLOUD_GCP_LOCATION=<location>`
- `REAL_CLOUD_GCP_KEY_RING=<pre-created sacrificial key ring>`

## Endpoints

- `GET /internal/real-cloud-execution/readiness`
- `POST /internal/real-cloud-execution/execute`

The execute endpoint is intentionally internal and is expected to remain BLOCKED until the dedicated Preview identity and sacrificial cloud resources are configured.
