-- Step 10B.25 — HSM/KMS-backed Root Custody + Root Ceremony + Offline Backup/Restore Drill

CREATE TABLE IF NOT EXISTS shrimp_bilibili_hsm_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,
  module_path text,
  token_label text,
  key_label text NOT NULL,
  key_id_hex text NOT NULL,
  algorithm text NOT NULL,
  public_key_pem_b64 text NOT NULL,
  fingerprint_sha256 char(64) NOT NULL UNIQUE,
  provider_key_locator jsonb NOT NULL,
  exportable_private_key boolean NOT NULL DEFAULT false,
  registered_by text NOT NULL,
  registered_at timestamptz NOT NULL DEFAULT now(),
  CHECK (provider IN ('PKCS11','OPENBAO_EXTERNAL_KEY','KMS_EXTERNAL')),
  CHECK (algorithm='ED25519'),
  CHECK (jsonb_typeof(provider_key_locator)='object'),
  CHECK (char_length(fingerprint_sha256)=64),
  CHECK (exportable_private_key=false)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_root_ceremonies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ceremony_type text NOT NULL,
  trust_root_version integer,
  participant_fingerprints jsonb NOT NULL,
  threshold integer NOT NULL,
  ceremony_manifest jsonb NOT NULL,
  ceremony_sha256 char(64) NOT NULL UNIQUE,
  ceremony_status text NOT NULL,
  executed_by text NOT NULL,
  executed_at timestamptz NOT NULL DEFAULT now(),
  CHECK (ceremony_type IN ('BOOTSTRAP','ROTATION','DISASTER_RECOVERY','RESTORE_VALIDATION')),
  CHECK (jsonb_typeof(participant_fingerprints)='array'),
  CHECK (jsonb_typeof(ceremony_manifest)='object'),
  CHECK (threshold>=1),
  CHECK (ceremony_status IN ('PASSED','FAILED')),
  CHECK (char_length(ceremony_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_offline_root_backups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trust_root_version integer NOT NULL,
  trust_root_sha256 char(64) NOT NULL,
  backup_manifest jsonb NOT NULL,
  backup_sha256 char(64) NOT NULL UNIQUE,
  contains_private_key boolean NOT NULL DEFAULT false,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(backup_manifest)='object'),
  CHECK (char_length(trust_root_sha256)=64),
  CHECK (char_length(backup_sha256)=64),
  CHECK (contains_private_key=false)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_root_restore_drills (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  backup_id uuid NOT NULL REFERENCES shrimp_bilibili_offline_root_backups(id) ON DELETE RESTRICT,
  restored_trust_root_sha256 char(64) NOT NULL,
  hsm_signature_verified boolean NOT NULL DEFAULT false,
  root_threshold_verified boolean NOT NULL DEFAULT false,
  private_key_export_observed boolean NOT NULL DEFAULT false,
  issue_codes jsonb NOT NULL,
  drill_snapshot jsonb NOT NULL,
  drill_sha256 char(64) NOT NULL UNIQUE,
  drill_status text NOT NULL,
  executed_by text NOT NULL,
  executed_at timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(issue_codes)='array'),
  CHECK (jsonb_typeof(drill_snapshot)='object'),
  CHECK (char_length(restored_trust_root_sha256)=64),
  CHECK (char_length(drill_sha256)=64),
  CHECK (drill_status IN ('PASSED','FAILED')),
  CHECK (private_key_export_observed=false)
);

CREATE OR REPLACE FUNCTION prevent_bilibili_step10b25_mutation()
RETURNS trigger AS $immutable$
BEGIN
  RAISE EXCEPTION 'Step 10B.25 records are immutable';
END;
$immutable$ LANGUAGE plpgsql;

DO $triggers$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'shrimp_bilibili_hsm_keys',
    'shrimp_bilibili_root_ceremonies',
    'shrimp_bilibili_offline_root_backups',
    'shrimp_bilibili_root_restore_drills'
  ]
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_step10b25_immutable ON %%I',t);
    EXECUTE format(
      'CREATE TRIGGER trg_step10b25_immutable BEFORE UPDATE OR DELETE ON %%I FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_step10b25_mutation()',
      t
    );
  END LOOP;
END
$triggers$;
