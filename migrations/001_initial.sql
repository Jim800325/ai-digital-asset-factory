CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status text NOT NULL DEFAULT 'RUNNING',
  pages_discovered int NOT NULL DEFAULT 0,
  pages_crawled int NOT NULL DEFAULT 0,
  evidence_created int NOT NULL DEFAULT 0,
  opportunities_created int NOT NULL DEFAULT 0,
  error text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE TABLE IF NOT EXISTS documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  url text NOT NULL UNIQUE,
  title text,
  content text NOT NULL,
  content_hash text NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  signal_type text NOT NULL,
  excerpt text NOT NULL,
  source_url text NOT NULL,
  confidence numeric(5,4) NOT NULL DEFAULT 0.5,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS digital_asset_opportunities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fingerprint text NOT NULL UNIQUE,
  title text NOT NULL,
  asset_type text NOT NULL CHECK (asset_type IN (
    'DATASET_API','INTELLIGENCE_REPORT','MICRO_SAAS_TOOL','TEMPLATE_WORKFLOW','CONTENT_IP'
  )),
  problem text,
  target_customer text,
  monetization_model text,
  source_url text NOT NULL,
  demand_score numeric(5,2) NOT NULL DEFAULT 0,
  repeatability_score numeric(5,2) NOT NULL DEFAULT 0,
  automation_score numeric(5,2) NOT NULL DEFAULT 0,
  ownership_score numeric(5,2) NOT NULL DEFAULT 0,
  marginal_cost_score numeric(5,2) NOT NULL DEFAULT 0,
  evidence_score numeric(5,2) NOT NULL DEFAULT 0,
  score numeric(5,2) NOT NULL DEFAULT 0,
  repeatable_sale boolean NOT NULL DEFAULT true,
  update_automation boolean NOT NULL DEFAULT true,
  status text NOT NULL DEFAULT 'WATCH',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_opportunities_score ON digital_asset_opportunities(score DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_signal ON evidence(signal_type);
