ALTER TABLE shrimp_animation_jobs
  ADD COLUMN IF NOT EXISTS render_artifact_sha256 char(64);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_render_artifact_sha256'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_render_artifact_sha256
      CHECK (
        render_artifact_sha256 IS NULL
        OR char_length(render_artifact_sha256)=64
      );
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS shrimp_animation_renders (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  composition_record_id uuid NOT NULL
    REFERENCES shrimp_animation_compositions(id) ON DELETE RESTRICT,
  animation_manifest_sha256 char(64) NOT NULL,
  props_sha256 char(64) NOT NULL,
  project_source_sha256 char(64) NOT NULL,
  render_adapter_key text NOT NULL,
  render_adapter_version text NOT NULL,
  command_sha256 char(64) NOT NULL,
  artifact_uri text NOT NULL,
  artifact_sha256 char(64) NOT NULL,
  byte_size bigint NOT NULL,
  media_type text NOT NULL,
  width integer NOT NULL,
  height integer NOT NULL,
  fps numeric(12,6) NOT NULL,
  frame_count integer NOT NULL,
  duration_ms integer NOT NULL,
  probe_content jsonb NOT NULL,
  provenance jsonb NOT NULL,
  render_status text NOT NULL DEFAULT 'CURRENT',
  created_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CHECK (char_length(animation_manifest_sha256)=64),
  CHECK (char_length(props_sha256)=64),
  CHECK (char_length(project_source_sha256)=64),
  CHECK (char_length(command_sha256)=64),
  CHECK (char_length(artifact_sha256)=64),
  CHECK (byte_size > 0),
  CHECK (media_type='video/mp4'),
  CHECK (width > 0 AND height > 0),
  CHECK (fps > 0),
  CHECK (frame_count > 0),
  CHECK (duration_ms > 0),
  CHECK (render_status IN ('CURRENT','STALE'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_animation_current_render
  ON shrimp_animation_renders(provider_job_id)
  WHERE render_status='CURRENT';

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_renders_job
  ON shrimp_animation_renders(provider_job_id,created_at DESC);

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_renders_composition
  ON shrimp_animation_renders(composition_record_id);

CREATE OR REPLACE FUNCTION prevent_shrimp_animation_render_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.provider_job_id IS DISTINCT FROM OLD.provider_job_id
     OR NEW.composition_record_id IS DISTINCT FROM OLD.composition_record_id
     OR NEW.animation_manifest_sha256 IS DISTINCT FROM OLD.animation_manifest_sha256
     OR NEW.props_sha256 IS DISTINCT FROM OLD.props_sha256
     OR NEW.project_source_sha256 IS DISTINCT FROM OLD.project_source_sha256
     OR NEW.render_adapter_key IS DISTINCT FROM OLD.render_adapter_key
     OR NEW.render_adapter_version IS DISTINCT FROM OLD.render_adapter_version
     OR NEW.command_sha256 IS DISTINCT FROM OLD.command_sha256
     OR NEW.artifact_uri IS DISTINCT FROM OLD.artifact_uri
     OR NEW.artifact_sha256 IS DISTINCT FROM OLD.artifact_sha256
     OR NEW.byte_size IS DISTINCT FROM OLD.byte_size
     OR NEW.media_type IS DISTINCT FROM OLD.media_type
     OR NEW.width IS DISTINCT FROM OLD.width
     OR NEW.height IS DISTINCT FROM OLD.height
     OR NEW.fps IS DISTINCT FROM OLD.fps
     OR NEW.frame_count IS DISTINCT FROM OLD.frame_count
     OR NEW.duration_ms IS DISTINCT FROM OLD.duration_ms
     OR NEW.probe_content IS DISTINCT FROM OLD.probe_content
     OR NEW.provenance IS DISTINCT FROM OLD.provenance
  THEN
    RAISE EXCEPTION 'Shrimp animation render identity/content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_animation_render_immutable
  ON shrimp_animation_renders;
CREATE TRIGGER trg_shrimp_animation_render_immutable
BEFORE UPDATE ON shrimp_animation_renders
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_animation_render_mutation();

CREATE OR REPLACE FUNCTION stale_shrimp_render_when_composition_stales()
RETURNS trigger AS $$
BEGIN
  IF OLD.composition_status='CURRENT'
     AND NEW.composition_status='STALE'
  THEN
    UPDATE shrimp_animation_renders
    SET render_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE composition_record_id=NEW.id
      AND render_status='CURRENT';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_stale_shrimp_render_with_composition
  ON shrimp_animation_compositions;
CREATE TRIGGER trg_stale_shrimp_render_with_composition
AFTER UPDATE OF composition_status ON shrimp_animation_compositions
FOR EACH ROW EXECUTE FUNCTION stale_shrimp_render_when_composition_stales();

CREATE OR REPLACE FUNCTION clear_shrimp_job_render_hash_when_render_stales()
RETURNS trigger AS $$
BEGIN
  IF OLD.render_status='CURRENT'
     AND NEW.render_status='STALE'
  THEN
    UPDATE shrimp_animation_jobs
    SET render_artifact_sha256=NULL,
        updated_at=now()
    WHERE provider_job_id=NEW.provider_job_id
      AND render_artifact_sha256=OLD.artifact_sha256;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_clear_shrimp_job_render_hash
  ON shrimp_animation_renders;
CREATE TRIGGER trg_clear_shrimp_job_render_hash
AFTER UPDATE OF render_status ON shrimp_animation_renders
FOR EACH ROW EXECUTE FUNCTION clear_shrimp_job_render_hash_when_render_stales();
