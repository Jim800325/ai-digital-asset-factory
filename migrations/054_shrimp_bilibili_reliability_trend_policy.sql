-- Step 10B.13 — Reliability Trend + Burn Rate + Regression + Policy Recommendations

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_trend_points (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_type text NOT NULL,
  account_id uuid
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  bucket_type text NOT NULL,
  bucket_start timestamptz NOT NULL,
  bucket_end timestamptz NOT NULL,
  source_scorecard_id uuid
    REFERENCES shrimp_bilibili_reliability_scorecards(id) ON DELETE RESTRICT,
  reliability_score numeric(6,2) NOT NULL,
  ack_success_rate numeric(6,2) NOT NULL,
  recovery_success_rate numeric(6,2) NOT NULL,
  ambiguity_rate_percent numeric(8,3) NOT NULL,
  circuit_open_count integer NOT NULL,
  recurring_root_cause_count integer NOT NULL,
  trend_payload jsonb NOT NULL,
  trend_sha256 char(64) NOT NULL UNIQUE,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (scope_type IN ('GLOBAL','ACCOUNT')),
  CHECK (
    (scope_type='GLOBAL' AND account_id IS NULL)
    OR (scope_type='ACCOUNT' AND account_id IS NOT NULL)
  ),
  CHECK (bucket_type IN ('DAILY','WEEKLY')),
  CHECK (bucket_end>bucket_start),
  CHECK (reliability_score>=0 AND reliability_score<=100),
  CHECK (ack_success_rate>=0 AND ack_success_rate<=100),
  CHECK (recovery_success_rate>=0 AND recovery_success_rate<=100),
  CHECK (ambiguity_rate_percent>=0),
  CHECK (circuit_open_count>=0),
  CHECK (recurring_root_cause_count>=0),
  CHECK (char_length(trend_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_reliability_trend_scope
  ON shrimp_bilibili_reliability_trend_points(
    scope_type,account_id,bucket_type,bucket_start DESC
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_error_budget_burn_evaluations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_type text NOT NULL,
  account_id uuid
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  short_window_hours integer NOT NULL,
  long_window_hours integer NOT NULL,
  ack_short_burn_rate numeric(12,4) NOT NULL,
  ack_long_burn_rate numeric(12,4) NOT NULL,
  recovery_short_burn_rate numeric(12,4) NOT NULL,
  recovery_long_burn_rate numeric(12,4) NOT NULL,
  burn_status text NOT NULL,
  evidence_payload jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL UNIQUE,
  generated_by text NOT NULL,
  CHECK (scope_type IN ('GLOBAL','ACCOUNT')),
  CHECK (
    (scope_type='GLOBAL' AND account_id IS NULL)
    OR (scope_type='ACCOUNT' AND account_id IS NOT NULL)
  ),
  CHECK (short_window_hours>=1),
  CHECK (long_window_hours>=short_window_hours),
  CHECK (ack_short_burn_rate>=0),
  CHECK (ack_long_burn_rate>=0),
  CHECK (recovery_short_burn_rate>=0),
  CHECK (recovery_long_burn_rate>=0),
  CHECK (burn_status IN ('HEALTHY','WATCH','FAST_BURN','EXHAUSTED')),
  CHECK (char_length(evidence_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_burn_scope
  ON shrimp_bilibili_error_budget_burn_evaluations(
    scope_type,account_id,evaluated_at DESC
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_regressions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_type text NOT NULL,
  account_id uuid
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  metric_key text NOT NULL,
  regression_status text NOT NULL DEFAULT 'OPEN',
  severity text NOT NULL,
  baseline_value numeric(14,4),
  current_value numeric(14,4),
  absolute_delta numeric(14,4),
  relative_delta_percent numeric(14,4),
  baseline_window_start timestamptz NOT NULL,
  baseline_window_end timestamptz NOT NULL,
  current_window_start timestamptz NOT NULL,
  current_window_end timestamptz NOT NULL,
  evidence_payload jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL UNIQUE,
  detected_by text NOT NULL,
  detected_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  CHECK (scope_type IN ('GLOBAL','ACCOUNT')),
  CHECK (
    (scope_type='GLOBAL' AND account_id IS NULL)
    OR (scope_type='ACCOUNT' AND account_id IS NOT NULL)
  ),
  CHECK (
    metric_key IN (
      'RELIABILITY_SCORE','ACK_SUCCESS_RATE','RECOVERY_SUCCESS_RATE',
      'AMBIGUITY_RATE','CIRCUIT_OPEN_COUNT','RECURRENCE_COUNT'
    )
  ),
  CHECK (regression_status IN ('OPEN','RESOLVED')),
  CHECK (severity IN ('INFO','WARNING','CRITICAL')),
  CHECK (char_length(evidence_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_regression_open
  ON shrimp_bilibili_reliability_regressions(
    regression_status,severity,detected_at DESC
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_policy_recommendations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_type text NOT NULL,
  account_id uuid
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  recommendation_key text NOT NULL,
  recommendation_status text NOT NULL DEFAULT 'OPEN',
  priority text NOT NULL,
  category text NOT NULL,
  title text NOT NULL,
  recommendation_text text NOT NULL,
  rationale text NOT NULL,
  evidence_payload jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  recommendation_sha256 char(64) NOT NULL UNIQUE,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CHECK (scope_type IN ('GLOBAL','ACCOUNT')),
  CHECK (
    (scope_type='GLOBAL' AND account_id IS NULL)
    OR (scope_type='ACCOUNT' AND account_id IS NOT NULL)
  ),
  CHECK (recommendation_status IN ('OPEN','SUPERSEDED')),
  CHECK (priority IN ('LOW','MEDIUM','HIGH','CRITICAL')),
  CHECK (
    category IN (
      'ACK_PROCESS','RECOVERY_PROCESS','PROVIDER_AMBIGUITY',
      'CIRCUIT_STABILITY','ROOT_CAUSE_RECURRENCE','ERROR_BUDGET'
    )
  ),
  CHECK (char_length(evidence_sha256)=64),
  CHECK (char_length(recommendation_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_recommendations_open
  ON shrimp_bilibili_reliability_policy_recommendations(
    recommendation_status,priority,generated_at DESC
  );

CREATE OR REPLACE FUNCTION prevent_bilibili_reliability_analysis_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili reliability analysis history is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_trend_mutation
  ON shrimp_bilibili_reliability_trend_points;
CREATE TRIGGER trg_prevent_bilibili_trend_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_reliability_trend_points
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_reliability_analysis_mutation();

DROP TRIGGER IF EXISTS trg_prevent_bilibili_burn_mutation
  ON shrimp_bilibili_error_budget_burn_evaluations;
CREATE TRIGGER trg_prevent_bilibili_burn_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_error_budget_burn_evaluations
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_reliability_analysis_mutation();
