-- Step 10B.21 — External Verification Anchor + Proof Bundle Signing + Independent Auditor Verification + Tamper-Evident Export Registry

CREATE TABLE IF NOT EXISTS shrimp_bilibili_audit_proof_bundles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  audit_proof_id uuid NOT NULL
    REFERENCES shrimp_bilibili_certification_audit_proofs(id) ON DELETE RESTRICT,
  bundle_snapshot jsonb NOT NULL,
  bundle_sha256 char(64) NOT NULL,
  signature_algorithm text NOT NULL,
  signature_b64 text NOT NULL,
  public_key_pem_b64 text NOT NULL,
  signing_key_fingerprint_sha256 char(64) NOT NULL,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(bundle_snapshot)='object'),
  CHECK (char_length(bundle_sha256)=64),
  CHECK (signature_algorithm IN ('ED25519')),
  CHECK (char_length(signing_key_fingerprint_sha256)=64),
  UNIQUE (bundle_sha256,signing_key_fingerprint_sha256)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_audit_proof_bundles
  ON shrimp_bilibili_audit_proof_bundles(generated_at DESC);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_external_verification_anchors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bundle_id uuid NOT NULL
    REFERENCES shrimp_bilibili_audit_proof_bundles(id) ON DELETE RESTRICT,
  anchor_provider text NOT NULL,
  anchor_reference text NOT NULL,
  anchor_digest_sha256 char(64) NOT NULL,
  receipt_snapshot jsonb NOT NULL,
  receipt_sha256 char(64) NOT NULL,
  anchor_sha256 char(64) NOT NULL UNIQUE,
  registered_by text NOT NULL,
  registered_at timestamptz NOT NULL DEFAULT now(),
  CHECK (char_length(anchor_digest_sha256)=64),
  CHECK (jsonb_typeof(receipt_snapshot)='object'),
  CHECK (char_length(receipt_sha256)=64),
  CHECK (char_length(anchor_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_external_verification_anchors
  ON shrimp_bilibili_external_verification_anchors(registered_at DESC);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_tamper_evident_export_registry (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  registry_sequence bigint NOT NULL UNIQUE,
  bundle_id uuid NOT NULL
    REFERENCES shrimp_bilibili_audit_proof_bundles(id) ON DELETE RESTRICT,
  previous_export_id uuid
    REFERENCES shrimp_bilibili_tamper_evident_export_registry(id) ON DELETE RESTRICT,
  previous_export_sha256 char(64),
  export_snapshot jsonb NOT NULL,
  export_sha256 char(64) NOT NULL UNIQUE,
  exported_by text NOT NULL,
  exported_at timestamptz NOT NULL DEFAULT now(),
  CHECK (registry_sequence>=1),
  CHECK (
    (previous_export_id IS NULL AND previous_export_sha256 IS NULL)
    OR
    (previous_export_id IS NOT NULL AND char_length(previous_export_sha256)=64)
  ),
  CHECK (jsonb_typeof(export_snapshot)='object'),
  CHECK (char_length(export_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_tamper_export_registry
  ON shrimp_bilibili_tamper_evident_export_registry(registry_sequence DESC);

CREATE OR REPLACE FUNCTION prevent_bilibili_audit_proof_bundle_mutation()
RETURNS trigger AS $proof_bundle$
BEGIN
  RAISE EXCEPTION 'Audit proof bundle is immutable';
END;
$proof_bundle$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_audit_proof_bundle_mutation
  ON shrimp_bilibili_audit_proof_bundles;
CREATE TRIGGER trg_prevent_bilibili_audit_proof_bundle_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_audit_proof_bundles
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_audit_proof_bundle_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_external_anchor_mutation()
RETURNS trigger AS $external_anchor$
BEGIN
  RAISE EXCEPTION 'External verification anchor is immutable';
END;
$external_anchor$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_external_anchor_mutation
  ON shrimp_bilibili_external_verification_anchors;
CREATE TRIGGER trg_prevent_bilibili_external_anchor_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_external_verification_anchors
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_external_anchor_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_export_registry_mutation()
RETURNS trigger AS $export_registry$
BEGIN
  RAISE EXCEPTION 'Tamper-evident export registry is immutable';
END;
$export_registry$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_export_registry_mutation
  ON shrimp_bilibili_tamper_evident_export_registry;
CREATE TRIGGER trg_prevent_bilibili_export_registry_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_tamper_evident_export_registry
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_export_registry_mutation();
