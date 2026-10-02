ALTER TABLE shrimp_animation_jobs
  ADD COLUMN IF NOT EXISTS assets_manifest_sha256 char(64),
  ADD COLUMN IF NOT EXISTS voices_manifest_sha256 char(64);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_assets_manifest_sha256'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_assets_manifest_sha256
      CHECK (
        assets_manifest_sha256 IS NULL
        OR char_length(assets_manifest_sha256)=64
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_voices_manifest_sha256'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_voices_manifest_sha256
      CHECK (
        voices_manifest_sha256 IS NULL
        OR char_length(voices_manifest_sha256)=64
      );
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS shrimp_animation_artifacts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  stage_key text NOT NULL,
  logical_key text NOT NULL,
  artifact_kind text NOT NULL,
  source_mode text NOT NULL,
  plan_sha256 char(64) NOT NULL,
  storage_uri text NOT NULL,
  media_type text NOT NULL,
  byte_size bigint NOT NULL,
  sha256 char(64) NOT NULL,
  license_id text NOT NULL,
  usage_rights text NOT NULL,
  adapter_key text NOT NULL,
  adapter_version text NOT NULL,
  provider_request_id text,
  provenance jsonb NOT NULL,
  duration_ms integer,
  verification_status text NOT NULL DEFAULT 'VERIFIED',
  is_current boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CHECK (stage_key IN ('ASSETS','VOICES')),
  CHECK (artifact_kind IN ('CHARACTER','BACKGROUND','VOICE')),
  CHECK (source_mode IN ('REUSED','GENERATED')),
  CHECK (usage_rights='APPROVED'),
  CHECK (verification_status='VERIFIED'),
  CHECK (byte_size > 0),
  CHECK (char_length(plan_sha256)=64),
  CHECK (char_length(sha256)=64),
  CHECK (duration_ms IS NULL OR duration_ms > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_animation_artifact_current
  ON shrimp_animation_artifacts(provider_job_id,stage_key,logical_key)
  WHERE is_current=true;

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_artifacts_job
  ON shrimp_animation_artifacts(provider_job_id,stage_key,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shrimp_animation_artifacts_sha
  ON shrimp_animation_artifacts(sha256);

CREATE OR REPLACE FUNCTION prevent_shrimp_animation_artifact_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.provider_job_id IS DISTINCT FROM OLD.provider_job_id
     OR NEW.stage_key IS DISTINCT FROM OLD.stage_key
     OR NEW.logical_key IS DISTINCT FROM OLD.logical_key
     OR NEW.artifact_kind IS DISTINCT FROM OLD.artifact_kind
     OR NEW.source_mode IS DISTINCT FROM OLD.source_mode
     OR NEW.plan_sha256 IS DISTINCT FROM OLD.plan_sha256
     OR NEW.storage_uri IS DISTINCT FROM OLD.storage_uri
     OR NEW.media_type IS DISTINCT FROM OLD.media_type
     OR NEW.byte_size IS DISTINCT FROM OLD.byte_size
     OR NEW.sha256 IS DISTINCT FROM OLD.sha256
     OR NEW.license_id IS DISTINCT FROM OLD.license_id
     OR NEW.usage_rights IS DISTINCT FROM OLD.usage_rights
     OR NEW.adapter_key IS DISTINCT FROM OLD.adapter_key
     OR NEW.adapter_version IS DISTINCT FROM OLD.adapter_version
     OR NEW.provider_request_id IS DISTINCT FROM OLD.provider_request_id
     OR NEW.provenance IS DISTINCT FROM OLD.provenance
     OR NEW.duration_ms IS DISTINCT FROM OLD.duration_ms
     OR NEW.verification_status IS DISTINCT FROM OLD.verification_status
  THEN
    RAISE EXCEPTION 'Shrimp animation artifact identity/content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_animation_artifact_immutable
  ON shrimp_animation_artifacts;
CREATE TRIGGER trg_shrimp_animation_artifact_immutable
BEFORE UPDATE ON shrimp_animation_artifacts
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_animation_artifact_mutation();
