CREATE TABLE IF NOT EXISTS live_acceptance_audits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trigger_token_sha256 text NOT NULL UNIQUE,
  acceptance_status text NOT NULL DEFAULT 'RUNNING',
  phase text NOT NULL DEFAULT 'claimed',
  provider text NOT NULL DEFAULT 'AIHUBMIX',
  model text NOT NULL,
  gateway_mode text NOT NULL DEFAULT 'PROXY',
  live_model_verified boolean NOT NULL DEFAULT false,
  budget_status text NOT NULL DEFAULT 'NOT_EVALUATED',
  gateway_request_count integer NOT NULL DEFAULT 0,
  prompt_tokens integer NOT NULL DEFAULT 0,
  completion_tokens integer NOT NULL DEFAULT 0,
  total_tokens integer NOT NULL DEFAULT 0,
  estimated_cost_usd numeric(12,6) NOT NULL DEFAULT 0,
  model_request_observed boolean NOT NULL DEFAULT false,
  tests_passed boolean NOT NULL DEFAULT false,
  artifact_count integer NOT NULL DEFAULT 0,
  source_tree_sha256 text,
  duplicate_requests integer NOT NULL DEFAULT 0,
  error text,
  result_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_live_acceptance_status
    CHECK (acceptance_status IN ('RUNNING','PASSED','FAILED')),
  CONSTRAINT chk_live_acceptance_budget_status
    CHECK (budget_status IN ('NOT_EVALUATED','WITHIN_BUDGET','BLOCKED')),
  CONSTRAINT chk_live_acceptance_nonnegative
    CHECK (
      gateway_request_count >= 0
      AND prompt_tokens >= 0
      AND completion_tokens >= 0
      AND total_tokens >= 0
      AND estimated_cost_usd >= 0
      AND artifact_count >= 0
      AND duplicate_requests >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_live_acceptance_audits_started
  ON live_acceptance_audits(started_at DESC);
