-- Controlled Production Release Executor v0.1 — Step 3
-- Third Human Production Execution Gate only.
-- PROMOTE persists authorization but performs no provider write.
-- ABORT may terminate a prepared MOCK execution safely.

ALTER TABLE production_release_execution_decisions
  ADD COLUMN IF NOT EXISTS deployment_plan_id uuid
    REFERENCES deployment_plans(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS plan_sha256 text,
  ADD COLUMN IF NOT EXISTS candidate_vercel_deployment_id text,
  ADD COLUMN IF NOT EXISTS candidate_vercel_url text,
  ADD COLUMN IF NOT EXISTS target_project_id text,
  ADD COLUMN IF NOT EXISTS target_team_id text,
  ADD COLUMN IF NOT EXISTS candidate_verified_event_id uuid
    REFERENCES production_release_execution_events(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS candidate_provider_result_sha256 text,
  ADD COLUMN IF NOT EXISTS integrity_check_id uuid
    REFERENCES production_release_execution_integrity_checks(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS decision_sha256 text,
  ADD COLUMN IF NOT EXISTS provider_write_performed boolean NOT NULL DEFAULT false
    CHECK (provider_write_performed=false),
  ADD COLUMN IF NOT EXISTS production_traffic_changed boolean NOT NULL DEFAULT false
    CHECK (production_traffic_changed=false);

CREATE UNIQUE INDEX IF NOT EXISTS
  uq_production_execution_decision_sha256
  ON production_release_execution_decisions(decision_sha256)
  WHERE decision_sha256 IS NOT NULL;

CREATE OR REPLACE FUNCTION enforce_production_release_execution_decision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  execution_row record;
  verified_event record;
  integrity_row record;
BEGIN
  SELECT * INTO execution_row
  FROM production_release_executions
  WHERE id=NEW.execution_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Production release execution not found';
  END IF;

  IF execution_row.execution_status<>'READY_FOR_PROMOTION' THEN
    RAISE EXCEPTION 'Human production execution decision requires READY_FOR_PROMOTION';
  END IF;

  IF NEW.execution_sha256<>execution_row.execution_sha256 THEN
    RAISE EXCEPTION 'Production execution SHA-256 mismatch';
  END IF;
  IF NEW.deployment_plan_id IS NULL
     OR NEW.deployment_plan_id<>execution_row.deployment_plan_id THEN
    RAISE EXCEPTION 'Production execution decision Deployment Plan mismatch';
  END IF;
  IF NEW.plan_sha256 IS NULL
     OR NEW.plan_sha256<>execution_row.plan_sha256 THEN
    RAISE EXCEPTION 'Production execution decision Plan SHA-256 mismatch';
  END IF;
  IF NEW.target_project_id IS NULL
     OR NEW.target_project_id<>execution_row.target_project_id
     OR NEW.target_team_id IS DISTINCT FROM execution_row.target_team_id THEN
    RAISE EXCEPTION 'Production execution decision target binding mismatch';
  END IF;

  IF execution_row.candidate_vercel_deployment_id IS NULL
     OR execution_row.candidate_vercel_url IS NULL THEN
    RAISE EXCEPTION 'Production execution decision requires persisted prepared candidate';
  END IF;
  IF NEW.candidate_vercel_deployment_id IS NULL
     OR NEW.candidate_vercel_deployment_id<>execution_row.candidate_vercel_deployment_id
     OR NEW.candidate_vercel_url IS NULL
     OR NEW.candidate_vercel_url<>execution_row.candidate_vercel_url THEN
    RAISE EXCEPTION 'Production execution decision candidate binding mismatch';
  END IF;

  SELECT id,provider_result_sha256 INTO verified_event
  FROM production_release_execution_events
  WHERE id=NEW.candidate_verified_event_id
    AND execution_id=NEW.execution_id
    AND event_type='CANDIDATE_VERIFIED';

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Production execution decision requires CANDIDATE_VERIFIED evidence';
  END IF;
  IF NEW.candidate_provider_result_sha256 IS NULL
     OR length(NEW.candidate_provider_result_sha256)<>64
     OR NEW.candidate_provider_result_sha256 IS DISTINCT FROM verified_event.provider_result_sha256 THEN
    RAISE EXCEPTION 'Production execution decision candidate verification hash mismatch';
  END IF;

  SELECT id,check_status,execution_sha256 INTO integrity_row
  FROM production_release_execution_integrity_checks
  WHERE id=NEW.integrity_check_id
    AND execution_id=NEW.execution_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Production execution decision requires integrity check evidence';
  END IF;
  IF integrity_row.execution_sha256<>execution_row.execution_sha256 THEN
    RAISE EXCEPTION 'Production execution decision integrity SHA-256 mismatch';
  END IF;
  IF NEW.decision='PROMOTE' AND integrity_row.check_status<>'VERIFIED' THEN
    RAISE EXCEPTION 'PROMOTE requires VERIFIED Execution Integrity Gate';
  END IF;

  IF NEW.decision_sha256 IS NULL
     OR length(NEW.decision_sha256)<>64 THEN
    RAISE EXCEPTION 'Production execution decision SHA-256 is required';
  END IF;

  IF NEW.provider_write_performed
     OR NEW.production_traffic_changed
     OR execution_row.production_execution_enabled
     OR execution_row.automatic_execution
     OR execution_row.automatic_promotion THEN
    RAISE EXCEPTION 'Step 3 human gate must not perform Production execution';
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_production_release_execution_decision
  ON production_release_execution_decisions;
CREATE TRIGGER trg_production_release_execution_decision
BEFORE INSERT ON production_release_execution_decisions
FOR EACH ROW EXECUTE FUNCTION enforce_production_release_execution_decision();

COMMENT ON TABLE production_release_execution_decisions IS
  'Step 3 immutable human PROMOTE/ABORT decisions bound to the frozen execution, prepared candidate, verified candidate evidence, and execution integrity check. No provider write is performed.';
