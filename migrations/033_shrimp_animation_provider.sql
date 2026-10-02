CREATE TABLE IF NOT EXISTS shrimp_animation_jobs (
  provider_job_id uuid PRIMARY KEY
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  episode_id text NOT NULL UNIQUE,
  schema_version text NOT NULL DEFAULT 'v0.1',
  planning_version text NOT NULL DEFAULT 'shrimp-planning-v0.1-deterministic',
  deterministic_seed char(64) NOT NULL,
  brief_sha256 char(64) NOT NULL,
  story_sha256 char(64),
  script_sha256 char(64),
  scene_sha256 char(64),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (char_length(deterministic_seed)=64),
  CHECK (char_length(brief_sha256)=64),
  CHECK (story_sha256 IS NULL OR char_length(story_sha256)=64),
  CHECK (script_sha256 IS NULL OR char_length(script_sha256)=64),
  CHECK (scene_sha256 IS NULL OR char_length(scene_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_jobs_episode
  ON shrimp_animation_jobs(episode_id);

CREATE OR REPLACE FUNCTION enforce_shrimp_animation_job_provider()
RETURNS trigger AS $$
DECLARE
  provider_key_value text;
  asset_class_value text;
BEGIN
  SELECT p.provider_key,p.asset_class
    INTO provider_key_value,asset_class_value
  FROM production_provider_jobs j
  JOIN production_provider_definitions p ON p.id=j.provider_id
  WHERE j.id=NEW.provider_job_id;

  IF provider_key_value IS DISTINCT FROM 'shrimp_animation' THEN
    RAISE EXCEPTION 'Shrimp animation job must reference shrimp_animation provider';
  END IF;
  IF asset_class_value IS DISTINCT FROM 'CONTENT_IP' THEN
    RAISE EXCEPTION 'Shrimp animation provider job must be CONTENT_IP';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_animation_job_provider
  ON shrimp_animation_jobs;
CREATE TRIGGER trg_shrimp_animation_job_provider
BEFORE INSERT OR UPDATE OF provider_job_id ON shrimp_animation_jobs
FOR EACH ROW EXECUTE FUNCTION enforce_shrimp_animation_job_provider();
