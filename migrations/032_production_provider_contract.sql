CREATE TABLE IF NOT EXISTS production_provider_definitions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_key text NOT NULL UNIQUE,
  asset_class text NOT NULL,
  provider_version text NOT NULL,
  contract_version text NOT NULL DEFAULT 'v0.1',
  execution_mode text NOT NULL DEFAULT 'SANDBOX_FIRST',
  external_publish_mode text NOT NULL DEFAULT 'HUMAN_GATED',
  stage_graph jsonb NOT NULL DEFAULT '[]'::jsonb,
  capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
  spec_sha256 char(64) NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (asset_class IN (
    'DATASET_API','INTELLIGENCE_REPORT','MICRO_SAAS_TOOL',
    'TEMPLATE_WORKFLOW','CONTENT_IP'
  )),
  CHECK (execution_mode IN ('SANDBOX_FIRST','INTERNAL_ONLY')),
  CHECK (external_publish_mode IN ('DISABLED','HUMAN_GATED')),
  CHECK (char_length(spec_sha256)=64)
);

CREATE TABLE IF NOT EXISTS production_provider_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id uuid NOT NULL
    REFERENCES production_provider_definitions(id) ON DELETE RESTRICT,
  build_proposal_id uuid NOT NULL
    REFERENCES build_proposals(id) ON DELETE CASCADE,
  proposal_revision integer NOT NULL,
  source_fingerprint text NOT NULL,
  asset_class text NOT NULL,
  job_status text NOT NULL DEFAULT 'READY',
  current_stage text,
  contract_snapshot jsonb NOT NULL,
  input_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  requires_human_build_approval boolean NOT NULL DEFAULT true,
  external_side_effects text NOT NULL DEFAULT 'DENY',
  production_execution_enabled boolean NOT NULL DEFAULT false,
  publish_enabled boolean NOT NULL DEFAULT false,
  requested_by text NOT NULL DEFAULT 'scheduler',
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz,
  stale_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  error text,
  UNIQUE(provider_id,build_proposal_id,proposal_revision,source_fingerprint),
  CHECK (proposal_revision >= 1),
  CHECK (asset_class IN (
    'DATASET_API','INTELLIGENCE_REPORT','MICRO_SAAS_TOOL',
    'TEMPLATE_WORKFLOW','CONTENT_IP'
  )),
  CHECK (job_status IN (
    'READY','RUNNING','WAITING_RETRY','ARTIFACT_READY','QC_PASSED',
    'COMPLETED','FAILED','BLOCKED','STALE','CANCELLED'
  )),
  CHECK (requires_human_build_approval=true),
  CHECK (external_side_effects='DENY'),
  CHECK (production_execution_enabled=false),
  CHECK (publish_enabled=false)
);

CREATE TABLE IF NOT EXISTS production_provider_job_stages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  stage_key text NOT NULL,
  stage_kind text NOT NULL DEFAULT 'OTHER',
  position integer NOT NULL,
  depends_on jsonb NOT NULL DEFAULT '[]'::jsonb,
  stage_status text NOT NULL DEFAULT 'PENDING',
  attempt_count integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 3,
  output_manifest_sha256 char(64),
  started_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_error text,
  UNIQUE(job_id,stage_key),
  UNIQUE(job_id,position),
  CHECK (position >= 0),
  CHECK (stage_kind IN ('PLAN','GENERATE','PACKAGE','QC','OTHER')),
  CHECK (stage_status IN (
    'PENDING','RUNNING','WAITING_RETRY','SUCCEEDED',
    'FAILED','STALE','BLOCKED','SKIPPED'
  )),
  CHECK (attempt_count >= 0),
  CHECK (max_attempts BETWEEN 1 AND 20),
  CHECK (
    output_manifest_sha256 IS NULL
    OR char_length(output_manifest_sha256)=64
  )
);

CREATE TABLE IF NOT EXISTS production_provider_manifests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  stage_key text NOT NULL,
  manifest_kind text NOT NULL,
  schema_version text NOT NULL DEFAULT 'v1',
  manifest_version integer NOT NULL DEFAULT 1,
  content jsonb NOT NULL,
  content_sha256 char(64) NOT NULL,
  is_current boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  UNIQUE(job_id,stage_key,manifest_kind,manifest_version),
  CHECK (manifest_version >= 1),
  CHECK (char_length(content_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_production_provider_manifest_current
  ON production_provider_manifests(job_id,stage_key,manifest_kind)
  WHERE is_current=true;

CREATE TABLE IF NOT EXISTS production_provider_resource_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  stage_key text,
  resource_type text NOT NULL,
  quantity numeric(20,6) NOT NULL DEFAULT 0,
  unit text NOT NULL,
  estimated_cost_usd numeric(14,6) NOT NULL DEFAULT 0,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  CHECK (resource_type IN (
    'CPU_SECONDS','GPU_SECONDS','MEMORY_GB_SECONDS','STORAGE_GB_HOURS',
    'MODEL_INPUT_TOKENS','MODEL_OUTPUT_TOKENS','NETWORK_BYTES','OTHER'
  )),
  CHECK (quantity >= 0),
  CHECK (estimated_cost_usd >= 0)
);

CREATE TABLE IF NOT EXISTS production_provider_events (
  id bigserial PRIMARY KEY,
  job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  stage_key text,
  event_type text NOT NULL,
  actor text NOT NULL DEFAULT 'system',
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_production_provider_definitions_active
  ON production_provider_definitions(active,asset_class,provider_key);
CREATE INDEX IF NOT EXISTS idx_production_provider_jobs_status
  ON production_provider_jobs(job_status,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_production_provider_jobs_proposal
  ON production_provider_jobs(build_proposal_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_production_provider_stages_job
  ON production_provider_job_stages(job_id,position);
CREATE INDEX IF NOT EXISTS idx_production_provider_manifests_job
  ON production_provider_manifests(job_id,stage_key,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_production_provider_resources_job
  ON production_provider_resource_events(job_id,recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_production_provider_events_job
  ON production_provider_events(job_id,created_at DESC);

CREATE OR REPLACE FUNCTION prevent_production_provider_manifest_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.job_id IS DISTINCT FROM OLD.job_id
     OR NEW.stage_key IS DISTINCT FROM OLD.stage_key
     OR NEW.manifest_kind IS DISTINCT FROM OLD.manifest_kind
     OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
     OR NEW.manifest_version IS DISTINCT FROM OLD.manifest_version
     OR NEW.content IS DISTINCT FROM OLD.content
     OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
  THEN
    RAISE EXCEPTION 'Production provider manifest content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_production_provider_manifest_immutable
  ON production_provider_manifests;
CREATE TRIGGER trg_production_provider_manifest_immutable
BEFORE UPDATE ON production_provider_manifests
FOR EACH ROW EXECUTE FUNCTION prevent_production_provider_manifest_mutation();
