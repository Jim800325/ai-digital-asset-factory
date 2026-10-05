-- Step 10B.26 — External KMS Provider Registry + Failover + Cross-KMS Ceremony

CREATE TABLE IF NOT EXISTS shrimp_bilibili_external_kms_providers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_type text NOT NULL,
  provider_ref text NOT NULL UNIQUE,
  key_locator jsonb NOT NULL,
  signing_algorithm text NOT NULL,
  priority integer NOT NULL DEFAULT 100,
  enabled boolean NOT NULL DEFAULT true,
  public_key_pem_b64 text,
  public_key_fingerprint_sha256 char(64),
  registered_by text NOT NULL,
  registered_at timestamptz NOT NULL DEFAULT now(),
  CHECK (provider_type IN ('AWS_KMS','GCP_KMS','AZURE_KEY_VAULT','OPENBAO_EXTERNAL_KEY')),
  CHECK (signing_algorithm='ECDSA_P256_SHA256'),
  CHECK (priority>=1),
  CHECK (jsonb_typeof(key_locator)='object'),
  CHECK (
    public_key_fingerprint_sha256 IS NULL
    OR char_length(public_key_fingerprint_sha256)=64
  )
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_external_kms_provider_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id uuid NOT NULL REFERENCES shrimp_bilibili_external_kms_providers(id) ON DELETE RESTRICT,
  event_type text NOT NULL,
  event_snapshot jsonb NOT NULL,
  event_sha256 char(64) NOT NULL UNIQUE,
  recorded_by text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  CHECK (event_type IN ('HEALTHY','UNHEALTHY','FAILOVER_SELECTED','FAILOVER_SKIPPED','DISABLED')),
  CHECK (jsonb_typeof(event_snapshot)='object'),
  CHECK (char_length(event_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_external_kms_failover_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_sha256 char(64) NOT NULL,
  attempted_provider_refs jsonb NOT NULL,
  selected_provider_ref text,
  failover_status text NOT NULL,
  failover_snapshot jsonb NOT NULL,
  failover_sha256 char(64) NOT NULL UNIQUE,
  executed_by text NOT NULL,
  executed_at timestamptz NOT NULL DEFAULT now(),
  CHECK (char_length(request_sha256)=64),
  CHECK (jsonb_typeof(attempted_provider_refs)='array'),
  CHECK (jsonb_typeof(failover_snapshot)='object'),
  CHECK (failover_status IN ('SUCCEEDED','FAILED')),
  CHECK (char_length(failover_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_cross_kms_root_ceremonies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trust_root_version integer NOT NULL,
  trust_root_sha256 char(64) NOT NULL,
  required_provider_threshold integer NOT NULL,
  provider_signatures jsonb NOT NULL,
  ceremony_manifest jsonb NOT NULL,
  ceremony_sha256 char(64) NOT NULL UNIQUE,
  ceremony_status text NOT NULL,
  executed_by text NOT NULL,
  executed_at timestamptz NOT NULL DEFAULT now(),
  CHECK (char_length(trust_root_sha256)=64),
  CHECK (required_provider_threshold>=2),
  CHECK (jsonb_typeof(provider_signatures)='array'),
  CHECK (jsonb_typeof(ceremony_manifest)='object'),
  CHECK (ceremony_status IN ('PASSED','FAILED')),
  CHECK (char_length(ceremony_sha256)=64)
);

CREATE OR REPLACE FUNCTION prevent_bilibili_step10b26_mutation()
RETURNS trigger AS $immutable$
BEGIN
  RAISE EXCEPTION 'Step 10B.26 records are immutable';
END;
$immutable$ LANGUAGE plpgsql;

DO $triggers$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'shrimp_bilibili_external_kms_providers',
    'shrimp_bilibili_external_kms_provider_events',
    'shrimp_bilibili_external_kms_failover_runs',
    'shrimp_bilibili_cross_kms_root_ceremonies'
  ]
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_step10b26_immutable ON %%I',t);
    EXECUTE format(
      'CREATE TRIGGER trg_step10b26_immutable BEFORE UPDATE OR DELETE ON %%I FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_step10b26_mutation()',
      t
    );
  END LOOP;
END
$triggers$;
