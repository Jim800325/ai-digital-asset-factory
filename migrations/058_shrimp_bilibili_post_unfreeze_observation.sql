-- Step 10B.17 — Post-Unfreeze Observation + Gradual Ramp + Restore Acceptance

ALTER TABLE shrimp_bilibili_reliability_policy_controls
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_reliability_policy_controls_automation_exposure_check;
ALTER TABLE shrimp_bilibili_reliability_policy_controls
  ADD CONSTRAINT shrimp_bilibili_reliability_policy_controls_automation_exposure_check
  CHECK (automation_exposure IN ('NORMAL','OBSERVATION','CAUTION','FROZEN'));

CREATE TABLE IF NOT EXISTS shrimp_bilibili_post_unfreeze_observation_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  restore_plan_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_reliability_restore_plans(id) ON DELETE RESTRICT,
  session_status text NOT NULL DEFAULT 'ACTIVE',
  current_stage integer NOT NULL DEFAULT 1,
  current_quota_percent integer NOT NULL DEFAULT 25,
  stage_started_at timestamptz NOT NULL DEFAULT now(),
  observation_started_at timestamptz NOT NULL DEFAULT now(),
  latest_evidence_sha256 char(64),
  refreeze_recommendation text,
  refreeze_reason text,
  completed_at timestamptz,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (session_status IN ('ACTIVE','REFREEZE_RECOMMENDED','READY_FOR_ACCEPTANCE','ACCEPTED','STALE')),
  CHECK (current_stage BETWEEN 1 AND 4),
  CHECK (current_quota_percent IN (25,50,75,100)),
  CHECK (
    refreeze_recommendation IS NULL
    OR refreeze_recommendation='REFREEZE_RECOMMENDED'
  ),
  CHECK (
    latest_evidence_sha256 IS NULL
    OR char_length(latest_evidence_sha256)=64
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_active_observation_session
  ON shrimp_bilibili_post_unfreeze_observation_sessions((1))
  WHERE session_status IN ('ACTIVE','REFREEZE_RECOMMENDED','READY_FOR_ACCEPTANCE');

CREATE TABLE IF NOT EXISTS shrimp_bilibili_post_unfreeze_ramp_evaluations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL
    REFERENCES shrimp_bilibili_post_unfreeze_observation_sessions(id) ON DELETE RESTRICT,
  stage_before integer NOT NULL,
  quota_before integer NOT NULL,
  decision text NOT NULL,
  recommended_next_stage integer,
  recommended_quota_percent integer,
  stage_elapsed_minutes integer NOT NULL,
  observed_execution_count integer NOT NULL,
  health_evidence_snapshot jsonb NOT NULL,
  health_evidence_sha256 char(64) NOT NULL,
  evaluation_sha256 char(64) NOT NULL UNIQUE,
  evaluated_by text NOT NULL,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (stage_before BETWEEN 1 AND 4),
  CHECK (quota_before IN (25,50,75,100)),
  CHECK (
    decision IN (
      'HOLD',
      'ADVANCE_TO_50',
      'ADVANCE_TO_75',
      'ADVANCE_TO_100',
      'READY_FOR_ACCEPTANCE',
      'REFREEZE_RECOMMENDED'
    )
  ),
  CHECK (
    recommended_next_stage IS NULL
    OR recommended_next_stage BETWEEN 1 AND 4
  ),
  CHECK (
    recommended_quota_percent IS NULL
    OR recommended_quota_percent IN (25,50,75,100)
  ),
  CHECK (stage_elapsed_minutes>=0),
  CHECK (observed_execution_count>=0),
  CHECK (jsonb_typeof(health_evidence_snapshot)='object'),
  CHECK (char_length(health_evidence_sha256)=64),
  CHECK (char_length(evaluation_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_ramp_session
  ON shrimp_bilibili_post_unfreeze_ramp_evaluations(
    session_id,evaluated_at DESC
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_restore_acceptances (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_post_unfreeze_observation_sessions(id) ON DELETE RESTRICT,
  acceptance_status text NOT NULL DEFAULT 'PENDING',
  evidence_snapshot jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  accepted_by text,
  accepted_at timestamptz,
  acceptance_sha256 char(64) UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (acceptance_status IN ('PENDING','ACCEPTED','REJECTED','STALE')),
  CHECK (jsonb_typeof(evidence_snapshot)='object'),
  CHECK (char_length(evidence_sha256)=64),
  CHECK (
    acceptance_sha256 IS NULL
    OR char_length(acceptance_sha256)=64
  )
);

CREATE OR REPLACE FUNCTION prevent_bilibili_ramp_evaluation_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Post-unfreeze ramp evaluation is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_ramp_evaluation_mutation
  ON shrimp_bilibili_post_unfreeze_ramp_evaluations;
CREATE TRIGGER trg_prevent_bilibili_ramp_evaluation_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_post_unfreeze_ramp_evaluations
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_ramp_evaluation_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_restore_acceptance_evidence_mutation()
RETURNS trigger AS $$
BEGIN
  IF OLD.acceptance_status='PENDING'
     AND NEW.acceptance_status IN ('ACCEPTED','REJECTED','STALE')
     AND NEW.evidence_snapshot=OLD.evidence_snapshot
     AND NEW.evidence_sha256=OLD.evidence_sha256
  THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Restore acceptance evidence is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_restore_acceptance_evidence_mutation
  ON shrimp_bilibili_restore_acceptances;
CREATE TRIGGER trg_prevent_bilibili_restore_acceptance_evidence_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_restore_acceptances
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_restore_acceptance_evidence_mutation();
