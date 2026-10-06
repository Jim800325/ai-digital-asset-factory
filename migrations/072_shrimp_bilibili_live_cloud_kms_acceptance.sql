-- Step 10B.27 — Controlled Live Cloud KMS Acceptance + Cleanup/Disable Verification

CREATE TABLE IF NOT EXISTS shrimp_bilibili_live_cloud_kms_acceptance_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_type text NOT NULL,
  provider_ref text NOT NULL,
  resource_locator jsonb NOT NULL,
  acceptance_snapshot jsonb NOT NULL,
  acceptance_sha256 char(64) NOT NULL UNIQUE,
  live_signature_verified boolean NOT NULL DEFAULT false,
  cleanup_requested boolean NOT NULL DEFAULT false,
  cleanup_verified boolean NOT NULL DEFAULT false,
  post_cleanup_sign_blocked boolean NOT NULL DEFAULT false,
  external_write_count integer NOT NULL DEFAULT 0,
  acceptance_status text NOT NULL,
  executed_by text NOT NULL,
  executed_at timestamptz NOT NULL DEFAULT now(),
  CHECK (provider_type IN ('AWS_KMS','GCP_KMS','AZURE_KEY_VAULT')),
  CHECK (jsonb_typeof(resource_locator)='object'),
  CHECK (jsonb_typeof(acceptance_snapshot)='object'),
  CHECK (char_length(acceptance_sha256)=64),
  CHECK (external_write_count>=0),
  CHECK (acceptance_status IN (
    'CREATED','SIGNED','VERIFIED','CLEANUP_REQUESTED','CLEANUP_VERIFIED','FAILED'
  ))
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_live_cloud_kms_outage_drills (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  primary_provider_ref text NOT NULL,
  fallback_provider_ref text NOT NULL,
  request_sha256 char(64) NOT NULL,
  outage_snapshot jsonb NOT NULL,
  outage_sha256 char(64) NOT NULL UNIQUE,
  primary_failure_observed boolean NOT NULL DEFAULT false,
  fallback_signature_verified boolean NOT NULL DEFAULT false,
  failover_status text NOT NULL,
  external_write_count integer NOT NULL DEFAULT 0,
  executed_by text NOT NULL,
  executed_at timestamptz NOT NULL DEFAULT now(),
  CHECK (char_length(request_sha256)=64),
  CHECK (jsonb_typeof(outage_snapshot)='object'),
  CHECK (char_length(outage_sha256)=64),
  CHECK (external_write_count>=0),
  CHECK (failover_status IN ('PASSED','FAILED'))
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_live_cross_cloud_ceremonies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trust_root_version integer NOT NULL,
  trust_root_sha256 char(64) NOT NULL,
  required_provider_threshold integer NOT NULL,
  live_provider_refs jsonb NOT NULL,
  provider_signatures jsonb NOT NULL,
  ceremony_snapshot jsonb NOT NULL,
  ceremony_sha256 char(64) NOT NULL UNIQUE,
  ceremony_status text NOT NULL,
  external_write_count integer NOT NULL DEFAULT 0,
  executed_by text NOT NULL,
  executed_at timestamptz NOT NULL DEFAULT now(),
  CHECK (char_length(trust_root_sha256)=64),
  CHECK (required_provider_threshold>=2),
  CHECK (jsonb_typeof(live_provider_refs)='array'),
  CHECK (jsonb_typeof(provider_signatures)='array'),
  CHECK (jsonb_typeof(ceremony_snapshot)='object'),
  CHECK (char_length(ceremony_sha256)=64),
  CHECK (external_write_count>=0),
  CHECK (ceremony_status IN ('PASSED','FAILED'))
);

CREATE OR REPLACE FUNCTION prevent_bilibili_step10b27_mutation()
RETURNS trigger AS $immutable$
BEGIN
  RAISE EXCEPTION 'Step 10B.27 records are immutable';
END;
$immutable$ LANGUAGE plpgsql;

DO $triggers$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'shrimp_bilibili_live_cloud_kms_acceptance_runs',
    'shrimp_bilibili_live_cloud_kms_outage_drills',
    'shrimp_bilibili_live_cross_cloud_ceremonies'
  ]
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_step10b27_immutable ON %%I',t);
    EXECUTE format(
      'CREATE TRIGGER trg_step10b27_immutable BEFORE UPDATE OR DELETE ON %%I FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_step10b27_mutation()',
      t
    );
  END LOOP;
END
$triggers$;
