CREATE TABLE IF NOT EXISTS side_business_composition_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status text NOT NULL DEFAULT 'RUNNING',
  providers_considered integer NOT NULL DEFAULT 0,
  compositions_generated integer NOT NULL DEFAULT 0,
  active_compositions integer NOT NULL DEFAULT 0,
  watch_compositions integer NOT NULL DEFAULT 0,
  blocked_compositions integer NOT NULL DEFAULT 0,
  stale_compositions integer NOT NULL DEFAULT 0,
  hypotheses_emitted integer NOT NULL DEFAULT 0,
  errors integer NOT NULL DEFAULT 0,
  error_summary text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CHECK (status IN ('RUNNING','SUCCESS','PARTIAL','FAILED','SKIPPED'))
);

CREATE TABLE IF NOT EXISTS side_business_compositions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  composition_key char(64) NOT NULL UNIQUE,
  template_id text NOT NULL,
  opportunity_title text NOT NULL,
  hypothesis text NOT NULL,
  target_customer text NOT NULL,
  monetization_model text NOT NULL,
  stack_score numeric(5,2) NOT NULL DEFAULT 0,
  license_compatibility text NOT NULL DEFAULT 'REVIEW',
  commercial_constraints jsonb NOT NULL DEFAULT '[]'::jsonb,
  operating_cost jsonb NOT NULL DEFAULT '{}'::jsonb,
  provider_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence_payload_sha256 char(64) NOT NULL,
  composition_status text NOT NULL DEFAULT 'WATCH',
  last_generated_run_id uuid REFERENCES side_business_composition_runs(id) ON DELETE SET NULL,
  last_generated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (license_compatibility IN ('PASS','REVIEW','BLOCKED')),
  CHECK (composition_status IN ('ACTIVE','WATCH','BLOCKED','STALE'))
);

CREATE TABLE IF NOT EXISTS side_business_composition_members (
  composition_id uuid NOT NULL REFERENCES side_business_compositions(id) ON DELETE CASCADE,
  provider_id uuid NOT NULL REFERENCES side_business_providers(id) ON DELETE CASCADE,
  slot text NOT NULL,
  provider_role text NOT NULL,
  provider_score numeric(5,2) NOT NULL DEFAULT 0,
  license_policy text NOT NULL,
  commercial_fit text NOT NULL,
  position integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (composition_id,provider_id),
  UNIQUE (composition_id,slot)
);

CREATE INDEX IF NOT EXISTS idx_side_business_compositions_status
  ON side_business_compositions(composition_status,stack_score DESC,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_side_business_compositions_template
  ON side_business_compositions(template_id,composition_status,stack_score DESC);
CREATE INDEX IF NOT EXISTS idx_side_business_composition_members_provider
  ON side_business_composition_members(provider_id,composition_id);
CREATE INDEX IF NOT EXISTS idx_side_business_composition_runs
  ON side_business_composition_runs(started_at DESC);
