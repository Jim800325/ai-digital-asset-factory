ALTER TABLE shrimp_animation_jobs
  ADD COLUMN IF NOT EXISTS animation_manifest_sha256 char(64),
  ADD COLUMN IF NOT EXISTS remotion_props_sha256 char(64);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_animation_manifest_sha256'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_animation_manifest_sha256
      CHECK (
        animation_manifest_sha256 IS NULL
        OR char_length(animation_manifest_sha256)=64
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_remotion_props_sha256'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_remotion_props_sha256
      CHECK (
        remotion_props_sha256 IS NULL
        OR char_length(remotion_props_sha256)=64
      );
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS shrimp_animation_compositions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  animation_manifest_sha256 char(64) NOT NULL,
  scene_manifest_sha256 char(64) NOT NULL,
  asset_manifest_sha256 char(64) NOT NULL,
  voice_manifest_sha256 char(64) NOT NULL,
  renderer_key text NOT NULL,
  renderer_version text NOT NULL,
  composition_id text NOT NULL,
  project_source_sha256 char(64) NOT NULL,
  props_uri text NOT NULL,
  props_sha256 char(64) NOT NULL,
  props_content jsonb NOT NULL,
  composition_status text NOT NULL DEFAULT 'CURRENT',
  created_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CHECK (char_length(animation_manifest_sha256)=64),
  CHECK (char_length(scene_manifest_sha256)=64),
  CHECK (char_length(asset_manifest_sha256)=64),
  CHECK (char_length(voice_manifest_sha256)=64),
  CHECK (char_length(project_source_sha256)=64),
  CHECK (char_length(props_sha256)=64),
  CHECK (composition_status IN ('CURRENT','STALE'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_animation_current_composition
  ON shrimp_animation_compositions(provider_job_id)
  WHERE composition_status='CURRENT';

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_compositions_job
  ON shrimp_animation_compositions(provider_job_id,created_at DESC);

CREATE OR REPLACE FUNCTION prevent_shrimp_animation_composition_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.provider_job_id IS DISTINCT FROM OLD.provider_job_id
     OR NEW.animation_manifest_sha256 IS DISTINCT FROM OLD.animation_manifest_sha256
     OR NEW.scene_manifest_sha256 IS DISTINCT FROM OLD.scene_manifest_sha256
     OR NEW.asset_manifest_sha256 IS DISTINCT FROM OLD.asset_manifest_sha256
     OR NEW.voice_manifest_sha256 IS DISTINCT FROM OLD.voice_manifest_sha256
     OR NEW.renderer_key IS DISTINCT FROM OLD.renderer_key
     OR NEW.renderer_version IS DISTINCT FROM OLD.renderer_version
     OR NEW.composition_id IS DISTINCT FROM OLD.composition_id
     OR NEW.project_source_sha256 IS DISTINCT FROM OLD.project_source_sha256
     OR NEW.props_uri IS DISTINCT FROM OLD.props_uri
     OR NEW.props_sha256 IS DISTINCT FROM OLD.props_sha256
     OR NEW.props_content IS DISTINCT FROM OLD.props_content
  THEN
    RAISE EXCEPTION 'Shrimp animation composition identity/content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_animation_composition_immutable
  ON shrimp_animation_compositions;
CREATE TRIGGER trg_shrimp_animation_composition_immutable
BEFORE UPDATE ON shrimp_animation_compositions
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_animation_composition_mutation();
