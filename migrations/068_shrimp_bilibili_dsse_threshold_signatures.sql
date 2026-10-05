-- Step 10B.24A — Detached DSSE threshold signatures.
-- Migration 067 remains immutable.

CREATE TABLE IF NOT EXISTS shrimp_bilibili_dsse_attestation_signatures (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attestation_id uuid NOT NULL REFERENCES shrimp_bilibili_dsse_attestations(id) ON DELETE RESTRICT,
  key_fingerprint_sha256 char(64) NOT NULL,
  signature_b64 text NOT NULL,
  public_key_pem_b64 text NOT NULL,
  signature_sha256 char(64) NOT NULL UNIQUE,
  signed_by text NOT NULL,
  signed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(attestation_id,key_fingerprint_sha256),
  CHECK (char_length(key_fingerprint_sha256)=64),
  CHECK (char_length(signature_sha256)=64)
);

CREATE OR REPLACE FUNCTION prevent_bilibili_dsse_signature_mutation()
RETURNS trigger AS $immutable$
BEGIN
  RAISE EXCEPTION 'DSSE attestation signatures are immutable';
END;
$immutable$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_dsse_signature_immutable
  ON shrimp_bilibili_dsse_attestation_signatures;
CREATE TRIGGER trg_dsse_signature_immutable
BEFORE UPDATE OR DELETE ON shrimp_bilibili_dsse_attestation_signatures
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_dsse_signature_mutation();
