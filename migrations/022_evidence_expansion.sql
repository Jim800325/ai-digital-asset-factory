CREATE TABLE IF NOT EXISTS evidence_expansion_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id uuid REFERENCES digital_asset_opportunities(id) ON DELETE SET NULL,
  plan_version text NOT NULL,
  run_status text NOT NULL DEFAULT 'RUNNING',
  requested_by text NOT NULL DEFAULT 'controlled-api',
  search_seed text NOT NULL,
  initial_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  final_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  queries_attempted integer NOT NULL DEFAULT 0,
  candidates_found integer NOT NULL DEFAULT 0,
  accepted_count integer NOT NULL DEFAULT 0,
  rejected_count integer NOT NULL DEFAULT 0,
  ambiguous_count integer NOT NULL DEFAULT 0,
  error text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_evidence_expansion_run_status
    CHECK (run_status IN ('RUNNING','SUCCEEDED','NO_MATCH','BLOCKED','FAILED')),
  CONSTRAINT chk_evidence_expansion_counts_nonnegative
    CHECK (
      queries_attempted >= 0
      AND candidates_found >= 0
      AND accepted_count >= 0
      AND rejected_count >= 0
      AND ambiguous_count >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_evidence_expansion_runs_opportunity
  ON evidence_expansion_runs(opportunity_id, started_at DESC);

CREATE TABLE IF NOT EXISTS evidence_expansion_results (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES evidence_expansion_runs(id) ON DELETE CASCADE,
  task_id text NOT NULL,
  provider text NOT NULL,
  query text NOT NULL,
  source_url text NOT NULL,
  source_domain text NOT NULL,
  source_class text NOT NULL,
  title text NOT NULL,
  excerpt text NOT NULL DEFAULT '',
  mapping_score numeric(6,4) NOT NULL DEFAULT 0,
  decision text NOT NULL,
  decision_reason text NOT NULL,
  ingested boolean NOT NULL DEFAULT false,
  resulting_opportunity_id uuid REFERENCES digital_asset_opportunities(id) ON DELETE SET NULL,
  result_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_evidence_expansion_result_url UNIQUE(run_id, source_url),
  CONSTRAINT chk_evidence_expansion_result_decision
    CHECK (decision IN ('ACCEPTED','REJECTED','AMBIGUOUS','DUPLICATE')),
  CONSTRAINT chk_evidence_expansion_mapping_score
    CHECK (mapping_score >= 0 AND mapping_score <= 1)
);

CREATE INDEX IF NOT EXISTS idx_evidence_expansion_results_run
  ON evidence_expansion_results(run_id, decision, created_at);
