ALTER TABLE shrimp_animation_jobs
  ADD COLUMN IF NOT EXISTS asset_plan_sha256 char(64),
  ADD COLUMN IF NOT EXISTS voice_plan_sha256 char(64);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_asset_plan_sha256'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_asset_plan_sha256
      CHECK (
        asset_plan_sha256 IS NULL
        OR char_length(asset_plan_sha256)=64
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_voice_plan_sha256'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_voice_plan_sha256
      CHECK (
        voice_plan_sha256 IS NULL
        OR char_length(voice_plan_sha256)=64
      );
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS animation_reusable_assets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_key text NOT NULL UNIQUE,
  asset_kind text NOT NULL,
  storage_uri text NOT NULL,
  media_type text NOT NULL DEFAULT 'image/png',
  sha256 char(64) NOT NULL,
  license_id text NOT NULL,
  provenance text NOT NULL,
  usage_rights text NOT NULL DEFAULT 'REVIEW_REQUIRED',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (asset_kind IN (
    'CHARACTER_BASE','CHARACTER_VARIANT','BACKGROUND','PROP'
  )),
  CHECK (usage_rights IN ('APPROVED','REVIEW_REQUIRED','BLOCKED')),
  CHECK (char_length(sha256)=64)
);

CREATE TABLE IF NOT EXISTS animation_voice_profiles (
  voice_profile_id text PRIMARY KEY,
  adapter_hint text NOT NULL DEFAULT 'UNBOUND',
  language text NOT NULL DEFAULT 'zh-CN',
  source_type text NOT NULL DEFAULT 'UNKNOWN',
  provenance text NOT NULL,
  usage_rights text NOT NULL DEFAULT 'REVIEW_REQUIRED',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (source_type IN (
    'SYNTHETIC','SELF_RECORDED','LICENSED',
    'CLONED_WITH_CONSENT','UNKNOWN'
  )),
  CHECK (usage_rights IN ('APPROVED','REVIEW_REQUIRED','BLOCKED'))
);

CREATE TABLE IF NOT EXISTS animation_character_registry (
  character_id text PRIMARY KEY,
  display_name text NOT NULL,
  base_asset_id uuid
    REFERENCES animation_reusable_assets(id) ON DELETE RESTRICT,
  default_scale numeric(8,4) NOT NULL DEFAULT 1.0,
  anchor_points jsonb NOT NULL DEFAULT '{}'::jsonb,
  voice_profile_id text
    REFERENCES animation_voice_profiles(voice_profile_id) ON DELETE SET NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (default_scale > 0 AND default_scale <= 10)
);

CREATE TABLE IF NOT EXISTS animation_character_variants (
  character_id text NOT NULL
    REFERENCES animation_character_registry(character_id) ON DELETE CASCADE,
  variant_name text NOT NULL,
  asset_id uuid NOT NULL
    REFERENCES animation_reusable_assets(id) ON DELETE RESTRICT,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (character_id,variant_name)
);

CREATE TABLE IF NOT EXISTS animation_background_registry (
  background_id text PRIMARY KEY,
  asset_id uuid
    REFERENCES animation_reusable_assets(id) ON DELETE RESTRICT,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS animation_action_registry (
  action_key text PRIMARY KEY,
  renderer_primitive text NOT NULL,
  parameter_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
  deterministic boolean NOT NULL DEFAULT true,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS animation_camera_registry (
  camera_key text PRIMARY KEY,
  renderer_primitive text NOT NULL,
  parameter_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
  deterministic boolean NOT NULL DEFAULT true,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shrimp_animation_resource_plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  plan_kind text NOT NULL,
  plan_version integer NOT NULL DEFAULT 1,
  schema_version text NOT NULL,
  planner_version text NOT NULL,
  source_manifest_sha256 char(64) NOT NULL,
  registry_snapshot_sha256 char(64) NOT NULL,
  content jsonb NOT NULL,
  content_sha256 char(64) NOT NULL,
  plan_status text NOT NULL DEFAULT 'CURRENT',
  created_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  UNIQUE(provider_job_id,plan_kind,plan_version),
  CHECK (plan_kind IN ('ASSET','VOICE')),
  CHECK (plan_version >= 1),
  CHECK (plan_status IN ('CURRENT','STALE')),
  CHECK (char_length(source_manifest_sha256)=64),
  CHECK (char_length(registry_snapshot_sha256)=64),
  CHECK (char_length(content_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_animation_current_resource_plan
  ON shrimp_animation_resource_plans(provider_job_id,plan_kind)
  WHERE plan_status='CURRENT';

CREATE INDEX IF NOT EXISTS idx_animation_assets_kind
  ON animation_reusable_assets(asset_kind,active,usage_rights);
CREATE INDEX IF NOT EXISTS idx_animation_character_voice
  ON animation_character_registry(voice_profile_id);
CREATE INDEX IF NOT EXISTS idx_shrimp_resource_plans_job
  ON shrimp_animation_resource_plans(provider_job_id,plan_kind,plan_version DESC);

CREATE OR REPLACE FUNCTION prevent_shrimp_resource_plan_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.provider_job_id IS DISTINCT FROM OLD.provider_job_id
     OR NEW.plan_kind IS DISTINCT FROM OLD.plan_kind
     OR NEW.plan_version IS DISTINCT FROM OLD.plan_version
     OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
     OR NEW.planner_version IS DISTINCT FROM OLD.planner_version
     OR NEW.source_manifest_sha256 IS DISTINCT FROM OLD.source_manifest_sha256
     OR NEW.registry_snapshot_sha256 IS DISTINCT FROM OLD.registry_snapshot_sha256
     OR NEW.content IS DISTINCT FROM OLD.content
     OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
  THEN
    RAISE EXCEPTION 'Shrimp resource plan content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_resource_plan_immutable
  ON shrimp_animation_resource_plans;
CREATE TRIGGER trg_shrimp_resource_plan_immutable
BEFORE UPDATE ON shrimp_animation_resource_plans
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_resource_plan_mutation();

INSERT INTO animation_action_registry(
  action_key,renderer_primitive,parameter_schema,deterministic,active)
VALUES
  ('idle','idle','{}'::jsonb,true,true),
  ('talk','talk','{}'::jsonb,true,true),
  ('walk_left','walk_left','{}'::jsonb,true,true),
  ('walk_right','walk_right','{}'::jsonb,true,true),
  ('enter','enter','{"direction":"string"}'::jsonb,true,true),
  ('exit','exit','{"direction":"string"}'::jsonb,true,true),
  ('jump','jump','{}'::jsonb,true,true),
  ('shake','shake','{}'::jsonb,true,true),
  ('nod','nod','{}'::jsonb,true,true),
  ('bow','bow','{}'::jsonb,true,true),
  ('turn','turn','{}'::jsonb,true,true),
  ('scale_pulse','scale_pulse','{}'::jsonb,true,true),
  ('hit_reaction','hit_reaction','{}'::jsonb,true,true),
  ('surprised','surprised','{}'::jsonb,true,true),
  ('angry','angry','{}'::jsonb,true,true),
  ('laugh','laugh','{}'::jsonb,true,true)
ON CONFLICT(action_key) DO NOTHING;

INSERT INTO animation_camera_registry(
  camera_key,renderer_primitive,parameter_schema,deterministic,active)
VALUES
  ('static','static','{}'::jsonb,true,true),
  ('pan','pan','{"from":"number","to":"number"}'::jsonb,true,true),
  ('zoom','zoom','{"from_scale":"number","to_scale":"number"}'::jsonb,true,true),
  ('push_in','push_in','{"from_scale":"number","to_scale":"number"}'::jsonb,true,true),
  ('pull_out','pull_out','{"from_scale":"number","to_scale":"number"}'::jsonb,true,true),
  ('shake','shake','{}'::jsonb,true,true),
  ('focus_left','focus_left','{}'::jsonb,true,true),
  ('focus_right','focus_right','{}'::jsonb,true,true)
ON CONFLICT(camera_key) DO NOTHING;
