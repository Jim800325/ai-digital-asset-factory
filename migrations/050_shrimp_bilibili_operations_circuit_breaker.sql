-- Step 10B.9 — Publisher Operations Console + Claim Escalation + Account Circuit Breaker

CREATE TABLE IF NOT EXISTS shrimp_bilibili_claim_escalations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_claim_id uuid NOT NULL
    REFERENCES shrimp_bilibili_execution_claims(id) ON DELETE CASCADE,
  execution_id uuid NOT NULL
    REFERENCES shrimp_animation_publish_executions(id) ON DELETE CASCADE,
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  escalation_level text NOT NULL,
  escalation_status text NOT NULL DEFAULT 'OPEN',
  detected_execution_status text NOT NULL,
  claim_age_minutes integer NOT NULL,
  ambiguity_count integer NOT NULL DEFAULT 0,
  recommended_action text NOT NULL,
  escalation_payload jsonb NOT NULL,
  escalation_sha256 char(64) NOT NULL UNIQUE,
  opened_by text NOT NULL,
  opened_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  resolution_reason text,
  CHECK (escalation_level IN ('INFO','WARNING','CRITICAL')),
  CHECK (escalation_status IN ('OPEN','RESOLVED')),
  CHECK (
    recommended_action IN (
      'OBSERVE',
      'UPLOAD_READBACK',
      'PUBLISH_READBACK',
      'MANUAL_REVIEW'
    )
  ),
  CHECK (claim_age_minutes>=0),
  CHECK (ambiguity_count>=0),
  CHECK (char_length(escalation_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_open_claim_escalation
  ON shrimp_bilibili_claim_escalations(execution_claim_id)
  WHERE escalation_status='OPEN';

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_claim_escalation_level
  ON shrimp_bilibili_claim_escalations(
    escalation_status,escalation_level,opened_at
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_account_circuit_breakers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE CASCADE,
  circuit_status text NOT NULL DEFAULT 'CLOSED',
  open_reason text,
  ambiguity_score integer NOT NULL DEFAULT 0,
  provider_failure_score integer NOT NULL DEFAULT 0,
  opened_at timestamptz,
  recovery_not_before timestamptz,
  closed_at timestamptz,
  last_evidence_type text,
  last_evidence_sha256 char(64),
  state_version integer NOT NULL DEFAULT 1,
  updated_by text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (circuit_status IN ('CLOSED','OPEN','RECOVERY_PENDING')),
  CHECK (ambiguity_score>=0),
  CHECK (provider_failure_score>=0),
  CHECK (state_version>=1),
  CHECK (
    last_evidence_sha256 IS NULL
    OR char_length(last_evidence_sha256)=64
  )
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_circuit_status
  ON shrimp_bilibili_account_circuit_breakers(
    circuit_status,recovery_not_before
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_circuit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE CASCADE,
  previous_status text NOT NULL,
  next_status text NOT NULL,
  event_type text NOT NULL,
  reason text NOT NULL,
  evidence_payload jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL UNIQUE,
  actor text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (previous_status IN ('CLOSED','OPEN','RECOVERY_PENDING')),
  CHECK (next_status IN ('CLOSED','OPEN','RECOVERY_PENDING')),
  CHECK (
    event_type IN (
      'AUTO_OPEN_AMBIGUITY',
      'AUTO_OPEN_PROVIDER_FAILURE',
      'RECOVERY_EVALUATION',
      'RECOVERY_PENDING',
      'AUTO_CLOSE_EVIDENCE',
      'MANUAL_OPEN',
      'MANUAL_RECOVERY_REQUEST'
    )
  ),
  CHECK (char_length(evidence_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_circuit_events_account
  ON shrimp_bilibili_circuit_events(account_id,created_at DESC);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_recovery_policy_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_status text NOT NULL DEFAULT 'RUNNING',
  stuck_claim_count integer NOT NULL DEFAULT 0,
  reconciled_count integer NOT NULL DEFAULT 0,
  still_ambiguous_count integer NOT NULL DEFAULT 0,
  escalation_count integer NOT NULL DEFAULT 0,
  circuits_opened integer NOT NULL DEFAULT 0,
  circuits_closed integer NOT NULL DEFAULT 0,
  summary_payload jsonb,
  summary_sha256 char(64),
  started_by text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CHECK (run_status IN ('RUNNING','SUCCEEDED','PARTIAL','FAILED')),
  CHECK (stuck_claim_count>=0),
  CHECK (reconciled_count>=0),
  CHECK (still_ambiguous_count>=0),
  CHECK (escalation_count>=0),
  CHECK (circuits_opened>=0),
  CHECK (circuits_closed>=0),
  CHECK (summary_sha256 IS NULL OR char_length(summary_sha256)=64)
);

CREATE OR REPLACE FUNCTION prevent_bilibili_circuit_event_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili circuit event log is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_circuit_event_mutation
  ON shrimp_bilibili_circuit_events;
CREATE TRIGGER trg_prevent_bilibili_circuit_event_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_circuit_events
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_circuit_event_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_recovery_policy_run_delete()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili recovery policy run history cannot be deleted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_recovery_policy_run_delete
  ON shrimp_bilibili_recovery_policy_runs;
CREATE TRIGGER trg_prevent_bilibili_recovery_policy_run_delete
BEFORE DELETE ON shrimp_bilibili_recovery_policy_runs
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_recovery_policy_run_delete();
