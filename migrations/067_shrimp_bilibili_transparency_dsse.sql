-- Step 10B.24 — DSSE Attestation + Trusted Time + Rekor-compatible Transparency + Offline Verification

CREATE TABLE IF NOT EXISTS shrimp_bilibili_dsse_attestations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_type text NOT NULL,
  subject_id text NOT NULL,
  statement_snapshot jsonb NOT NULL,
  statement_sha256 char(64) NOT NULL,
  dsse_envelope jsonb NOT NULL,
  envelope_sha256 char(64) NOT NULL UNIQUE,
  signature_threshold integer NOT NULL,
  signer_fingerprints jsonb NOT NULL,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(statement_snapshot)='object'),
  CHECK (jsonb_typeof(dsse_envelope)='object'),
  CHECK (jsonb_typeof(signer_fingerprints)='array'),
  CHECK (signature_threshold>=1),
  CHECK (char_length(statement_sha256)=64),
  CHECK (char_length(envelope_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_trusted_timestamps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attestation_id uuid NOT NULL REFERENCES shrimp_bilibili_dsse_attestations(id) ON DELETE RESTRICT,
  timestamp_source text NOT NULL,
  trusted_time timestamptz NOT NULL,
  timestamp_response_b64 text,
  timestamp_chain_pem text,
  timestamp_snapshot jsonb NOT NULL,
  timestamp_sha256 char(64) NOT NULL UNIQUE,
  recorded_by text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  CHECK (timestamp_source IN ('RFC3161_TSA','TRANSPARENCY_LOG_INTEGRATED_TIME')),
  CHECK (jsonb_typeof(timestamp_snapshot)='object'),
  CHECK (char_length(timestamp_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_transparency_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attestation_id uuid NOT NULL REFERENCES shrimp_bilibili_dsse_attestations(id) ON DELETE RESTRICT,
  provider text NOT NULL,
  entry_uuid text,
  log_index bigint NOT NULL,
  tree_size bigint NOT NULL,
  root_hash text NOT NULL,
  inclusion_hashes jsonb NOT NULL,
  checkpoint text NOT NULL,
  integrated_time timestamptz NOT NULL,
  canonicalized_body_b64 text NOT NULL,
  receipt_snapshot jsonb NOT NULL,
  receipt_sha256 char(64) NOT NULL UNIQUE,
  recorded_by text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  CHECK (provider IN ('REKOR_V1','REKOR_COMPATIBLE')),
  CHECK (log_index>=0),
  CHECK (tree_size>=1),
  CHECK (jsonb_typeof(inclusion_hashes)='array'),
  CHECK (jsonb_typeof(receipt_snapshot)='object'),
  CHECK (char_length(receipt_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_offline_verification_bundles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attestation_id uuid NOT NULL REFERENCES shrimp_bilibili_dsse_attestations(id) ON DELETE RESTRICT,
  bundle_snapshot jsonb NOT NULL,
  bundle_sha256 char(64) NOT NULL UNIQUE,
  verification_status text NOT NULL,
  issue_codes jsonb NOT NULL,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(bundle_snapshot)='object'),
  CHECK (verification_status IN ('PASS','FAIL')),
  CHECK (jsonb_typeof(issue_codes)='array'),
  CHECK (char_length(bundle_sha256)=64)
);

CREATE OR REPLACE FUNCTION prevent_bilibili_step10b24_mutation()
RETURNS trigger AS $immutable$
BEGIN
  RAISE EXCEPTION 'Step 10B.24 records are immutable';
END;
$immutable$ LANGUAGE plpgsql;

DO $triggers$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'shrimp_bilibili_dsse_attestations',
    'shrimp_bilibili_trusted_timestamps',
    'shrimp_bilibili_transparency_entries',
    'shrimp_bilibili_offline_verification_bundles'
  ]
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_step10b24_immutable ON %%I',t);
    EXECUTE format(
      'CREATE TRIGGER trg_step10b24_immutable BEFORE UPDATE OR DELETE ON %%I FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_step10b24_mutation()',
      t
    );
  END LOOP;
END
$triggers$;
