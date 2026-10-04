-- Step 10B.12 — Incident Metrics + SLO/Error Budget + Reliability Scorecard + Recurrence Detection

CREATE TABLE IF NOT EXISTS shrimp_bilibili_recurrence_clusters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  root_cause_fingerprint char(64) NOT NULL UNIQUE,
  normalized_root_cause text NOT NULL,
  recurrence_status text NOT NULL DEFAULT 'OBSERVED',
  occurrence_count integer NOT NULL DEFAULT 1,
  critical_occurrence_count integer NOT NULL DEFAULT 0,
  incident_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  account_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
  first_observed_at timestamptz NOT NULL,
  last_observed_at timestamptz NOT NULL,
  latest_pir_completed_at timestamptz,
  evidence_payload jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  detected_by text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    recurrence_status IN ('OBSERVED','WATCH','RECURRING','RESOLVED')
  ),
  CHECK (occurrence_count>=1),
  CHECK (critical_occurrence_count>=0),
  CHECK (char_length(root_cause_fingerprint)=64),
  CHECK (char_length(evidence_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_recurrence_status
  ON shrimp_bilibili_recurrence_clusters(
    recurrence_status,occurrence_count DESC,last_observed_at DESC
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_scorecards (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_type text NOT NULL DEFAULT 'GLOBAL',
  account_id uuid
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  window_start timestamptz NOT NULL,
  window_end timestamptz NOT NULL,
  window_days integer NOT NULL,
  incident_count integer NOT NULL,
  resolved_incident_count integer NOT NULL,
  acknowledged_incident_count integer NOT NULL,
  ack_slo_success_count integer NOT NULL,
  ack_slo_breach_count integer NOT NULL,
  recovery_slo_success_count integer NOT NULL,
  recovery_slo_breach_count integer NOT NULL,
  avg_ack_minutes numeric(12,2),
  p95_ack_minutes numeric(12,2),
  avg_mttr_minutes numeric(12,2),
  p95_mttr_minutes numeric(12,2),
  circuit_open_count integer NOT NULL,
  provider_ambiguity_count integer NOT NULL,
  publisher_execution_count integer NOT NULL,
  ambiguity_rate_percent numeric(8,3) NOT NULL,
  recurring_root_cause_count integer NOT NULL,
  ack_slo_target_percent numeric(6,2) NOT NULL,
  recovery_slo_target_percent numeric(6,2) NOT NULL,
  ack_error_budget_allowed integer NOT NULL,
  ack_error_budget_consumed integer NOT NULL,
  ack_error_budget_remaining integer NOT NULL,
  recovery_error_budget_allowed integer NOT NULL,
  recovery_error_budget_consumed integer NOT NULL,
  recovery_error_budget_remaining integer NOT NULL,
  reliability_score numeric(6,2) NOT NULL,
  reliability_grade text NOT NULL,
  metrics_payload jsonb NOT NULL,
  scorecard_sha256 char(64) NOT NULL UNIQUE,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (scope_type IN ('GLOBAL','ACCOUNT')),
  CHECK (
    (scope_type='GLOBAL' AND account_id IS NULL)
    OR (scope_type='ACCOUNT' AND account_id IS NOT NULL)
  ),
  CHECK (window_days>=1),
  CHECK (incident_count>=0),
  CHECK (resolved_incident_count>=0),
  CHECK (acknowledged_incident_count>=0),
  CHECK (ack_slo_success_count>=0),
  CHECK (ack_slo_breach_count>=0),
  CHECK (recovery_slo_success_count>=0),
  CHECK (recovery_slo_breach_count>=0),
  CHECK (circuit_open_count>=0),
  CHECK (provider_ambiguity_count>=0),
  CHECK (publisher_execution_count>=0),
  CHECK (ambiguity_rate_percent>=0),
  CHECK (recurring_root_cause_count>=0),
  CHECK (reliability_score>=0 AND reliability_score<=100),
  CHECK (reliability_grade IN ('A','B','C','D','F')),
  CHECK (char_length(scorecard_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_scorecard_scope
  ON shrimp_bilibili_reliability_scorecards(
    scope_type,account_id,generated_at DESC
  );

CREATE OR REPLACE FUNCTION prevent_bilibili_scorecard_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili reliability scorecard is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_scorecard_mutation
  ON shrimp_bilibili_reliability_scorecards;
CREATE TRIGGER trg_prevent_bilibili_scorecard_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_reliability_scorecards
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_scorecard_mutation();
