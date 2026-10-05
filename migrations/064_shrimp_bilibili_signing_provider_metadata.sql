-- Step 10B.22A — OpenBao provider metadata + TUF trust-root transition metadata
-- Migration 063 is immutable: this migration only evolves the schema forward.

ALTER TABLE shrimp_bilibili_signing_keys
  ADD COLUMN IF NOT EXISTS provider text NOT NULL DEFAULT 'LOCAL_PEM',
  ADD COLUMN IF NOT EXISTS provider_key_name text NOT NULL DEFAULT 'environment',
  ADD COLUMN IF NOT EXISTS provider_key_version integer NOT NULL DEFAULT 1;

ALTER TABLE shrimp_bilibili_signing_trust_roots
  ADD COLUMN IF NOT EXISTS transition_type text NOT NULL DEFAULT 'BOOTSTRAP';

DO $constraints$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='shrimp_bilibili_signing_keys_provider_check'
  ) THEN
    ALTER TABLE shrimp_bilibili_signing_keys
      ADD CONSTRAINT shrimp_bilibili_signing_keys_provider_check
      CHECK (provider IN ('LOCAL_PEM','OPENBAO_TRANSIT'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='shrimp_bilibili_signing_keys_provider_version_check'
  ) THEN
    ALTER TABLE shrimp_bilibili_signing_keys
      ADD CONSTRAINT shrimp_bilibili_signing_keys_provider_version_check
      CHECK (provider_key_version>=1);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='shrimp_bilibili_signing_trust_roots_transition_check'
  ) THEN
    ALTER TABLE shrimp_bilibili_signing_trust_roots
      ADD CONSTRAINT shrimp_bilibili_signing_trust_roots_transition_check
      CHECK (
        transition_type IN (
          'BOOTSTRAP','ROTATION','REVOCATION','POLICY_UPDATE'
        )
      );
  END IF;
END
$constraints$;
