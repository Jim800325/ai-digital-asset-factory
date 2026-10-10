# Step 10B.27A Cloud Identity Provisioning

This directory contains reviewed infrastructure templates for the real-cloud acceptance gate.

## Safety model

- Preview-only Vercel OIDC.
- AWS trust is restricted to the expected Vercel audience, project ID, owner ID and `environment=preview`.
- AWS KMS creation requires the `shrimp-live-acceptance=true` request tag; subsequent KMS actions require the same resource tag.
- GCP uses Workload Identity Federation and a dedicated service account.
- GCP provider attribute conditions restrict the Vercel project, owner and Preview environment.
- GCP uses a project custom role instead of Owner or KMS Admin.
- No Production environment variables are created.
- No static AWS/GCP credentials are required.

## AWS

Run from a dedicated sacrificial AWS account or isolated test account:

```bash
cd infra/step10b27a/aws
terraform init
terraform plan -var="aws_region=us-east-1"
terraform apply -var="aws_region=us-east-1"
```

Capture only:
- `REAL_CLOUD_AWS_ROLE_ARN`
- `REAL_CLOUD_AWS_REGION`

Do not copy access keys into Vercel.

## GCP

Use a dedicated sacrificial GCP project:

```bash
cd infra/step10b27a/gcp
terraform init
terraform plan -var="project_id=<SACRIFICIAL_PROJECT_ID>"
terraform apply -var="project_id=<SACRIFICIAL_PROJECT_ID>"
```

Capture:
- `REAL_CLOUD_GCP_WORKLOAD_IDENTITY_AUDIENCE`
- `REAL_CLOUD_GCP_SERVICE_ACCOUNT`
- `REAL_CLOUD_GCP_PROJECT_ID`
- `REAL_CLOUD_GCP_LOCATION`
- `REAL_CLOUD_GCP_KEY_RING`

## Activation order

1. Provision AWS and GCP identities/resources.
2. Add the seven outputs to the branch-scoped Vercel Preview environment.
3. Keep `REAL_CLOUD_EXECUTION_ENABLED=false`.
4. Keep `REAL_CLOUD_CLEANUP_ENABLED=false`.
5. Redeploy Preview and verify both providers are configured.
6. Enable cleanup only after identity verification.
7. Enable execution last.
8. Run each provider acceptance independently before cross-cloud failover.

Production remains unchanged throughout this procedure.
