-- Step 10B.22 — Signing Key Rotation + Revocation Registry + Multi-Key Verification + Historical Proof Validity

CREATE TABLE IF NOT EXISTS shrimp_bilibili_signing_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key_fingerprint_sha256 char(64) NOT NULL UNIQUE,
  algorithm text NOT NULL,
  public_key_pem_b64 text NOT NULL,
  key_label text NOT NULL,
  registered_by text NOT NULL,
  registered_at timestamptz NOT NULL DEFAULT now(),
  CHECK (algorithm='ED25519'),
  CHECK (char_length(key_fingerprint_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_signing_key_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key_id uuid NOT NULL
    REFERENCES shrimp_bilibili_signing_keys(id) ON DELETE RESTRICT,
  previous_key_id uuid
    REFERENCES shrimp_bilibili_signing_keys(id) ON DELETE RESTRICT,
  event_type text NOT NULL,
  effective_at timestamptz NOT NULL,
  reason text NOT NULL,
  event_snapshot jsonb NOT NULL,
  event_sha256 char(64) NOT NULL UNIQUE,
  recorded_by text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  CHECK (event_type IN ('ACTIVATED','ROTATED_IN','RETIRED','REVOKED')),
  CHECK (jsonb_typeof(event_snapshot)='object'),
  CHECK (char_length(event_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_signing_key_events
  ON shrimp_bilibili_signing_key_events(key_id,effective_at DESC,recorded_at DESC);



CREATE TABLE IF NOT EXISTS shrimp_bilibili_signing_trust_roots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  root_version integer NOT NULL UNIQUE,
  previous_root_id uuid
    REFERENCES shrimp_bilibili_signing_trust_roots(id) ON DELETE RESTRICT,
  root_threshold integer NOT NULL,
  authorized_key_fingerprints jsonb NOT NULL,
  root_snapshot jsonb NOT NULL,
  root_sha256 char(64) NOT NULL UNIQUE,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (root_version>=1),
  CHECK (root_threshold>=1),
  CHECK (jsonb_typeof(authorized_key_fingerprints)='array'),
  CHECK (jsonb_typeof(root_snapshot)='object'),
  CHECK (char_length(root_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_signing_trust_roots
  ON shrimp_bilibili_signing_trust_roots(root_version DESC);

CREATE OR REPLACE FUNCTION prevent_bilibili_signing_key_mutation()
RETURNS trigger AS $signing_key$
BEGIN
  RAISE EXCEPTION 'Signing key registry is immutable';
END;
$signing_key$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_signing_key_mutation
  ON shrimp_bilibili_signing_keys;
CREATE TRIGGER trg_prevent_bilibili_signing_key_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_signing_keys
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_signing_key_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_signing_key_event_mutation()
RETURNS trigger AS $signing_key_event$
BEGIN
  RAISE EXCEPTION 'Signing key lifecycle event is immutable';
END;
$signing_key_event$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_signing_key_event_mutation
  ON shrimp_bilibili_signing_key_events;
CREATE TRIGGER trg_prevent_bilibili_signing_key_event_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_signing_key_events
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_signing_key_event_mutation();


CREATE OR REPLACE FUNCTION prevent_bilibili_signing_trust_root_mutation()
RETURNS trigger AS $signing_trust_root$
BEGIN
  RAISE EXCEPTION 'Signing trust root is immutable';
END;
$signing_trust_root$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_signing_trust_root_mutation
  ON shrimp_bilibili_signing_trust_roots;
CREATE TRIGGER trg_prevent_bilibili_signing_trust_root_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_signing_trust_roots
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_signing_trust_root_mutation();
