-- Controlled Production Release Executor v0.1 — Step 4A
-- Live Preview / sacrificial Vercel PREPARE acceptance audit.
-- This migration records acceptance evidence only. It does not enable promotion.

CREATE TABLE IF NOT EXISTS vercel_prepare_acceptance_runs (
  id uuid PRIMARY KEY,
  acceptance_status text NOT NULL
    CHECK (acceptance_status IN (
      'CREATED','PRECHECKED','FIXTURE_READY','EXECUTION_CREATED',
      'PREPARE_UNKNOWN','PREPARE_PENDING','READY_FOR_PROMOTION',
      'PROMOTE_AUTHORIZED','BLOCKED','FAILED','CLEANED_UP'
    )),
  source_commit text NOT NULL,
  control_preview_url text,
  live_acceptance_audit_id text NOT NULL,
  source_fixture_request_id uuid
    REFERENCES sandbox_build_requests(id) ON DELETE RESTRICT,
  acceptance_request_id uuid
    REFERENCES sandbox_build_requests(id) ON DELETE RESTRICT,
  release_candidate_id uuid
    REFERENCES release_candidates(id) ON DELETE RESTRICT,
  review_package_id uuid
    REFERENCES release_review_packages(id) ON DELETE RESTRICT,
  deployment_plan_id uuid
    REFERENCES deployment_plans(id) ON DELETE RESTRICT,
  execution_id uuid
    REFERENCES production_release_executions(id) ON DELETE RESTRICT,

  sacrificial_project_id text NOT NULL,
  sacrificial_team_id text NOT NULL,
  real_project_id text NOT NULL,
  real_project_denylist_verified boolean NOT NULL DEFAULT false,
  target_lookup_verified boolean NOT NULL DEFAULT false,

  execution_sha256 text,
  candidate_vercel_deployment_id text,
  candidate_vercel_url text,
  prepare_outcome text,
  prepare_write_count integer,
  human_decision_id uuid
    REFERENCES production_release_execution_decisions(id) ON DELETE RESTRICT,
  decision_sha256 text,

  provider_write_performed boolean NOT NULL DEFAULT false,
  production_traffic_changed boolean NOT NULL DEFAULT false
    CHECK (production_traffic_changed=false),
  production_promotion_performed boolean NOT NULL DEFAULT false
    CHECK (production_promotion_performed=false),
  production_rollback_performed boolean NOT NULL DEFAULT false
    CHECK (production_rollback_performed=false),

  blocking_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS
  uq_vercel_prepare_acceptance_target_commit
  ON vercel_prepare_acceptance_runs(
    source_commit,
    sacrificial_project_id,
    sacrificial_team_id
  );

CREATE OR REPLACE FUNCTION enforce_vercel_prepare_acceptance_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.real_project_id='prj_orLCRCIm7aVfImH8ihB3gponFOEl'
     AND NEW.sacrificial_project_id=NEW.real_project_id THEN
    RAISE EXCEPTION 'Sacrificial target cannot be the real Production project';
  END IF;

  IF NEW.production_traffic_changed
     OR NEW.production_promotion_performed
     OR NEW.production_rollback_performed THEN
    RAISE EXCEPTION 'Step 4A acceptance must not change Production traffic';
  END IF;

  IF NEW.prepare_write_count IS NOT NULL
     AND (NEW.prepare_write_count<0 OR NEW.prepare_write_count>1) THEN
    RAISE EXCEPTION 'Step 4A PREPARE provider write count must be zero or one';
  END IF;

  IF NEW.execution_sha256 IS NOT NULL
     AND length(NEW.execution_sha256)<>64 THEN
    RAISE EXCEPTION 'Step 4A execution SHA-256 is invalid';
  END IF;

  IF NEW.decision_sha256 IS NOT NULL
     AND length(NEW.decision_sha256)<>64 THEN
    RAISE EXCEPTION 'Step 4A decision SHA-256 is invalid';
  END IF;

  IF TG_OP='UPDATE' THEN
    IF NEW.id<>OLD.id
       OR NEW.source_commit<>OLD.source_commit
       OR NEW.live_acceptance_audit_id<>OLD.live_acceptance_audit_id
       OR NEW.sacrificial_project_id<>OLD.sacrificial_project_id
       OR NEW.sacrificial_team_id<>OLD.sacrificial_team_id
       OR NEW.real_project_id<>OLD.real_project_id
       OR NEW.created_at<>OLD.created_at THEN
      RAISE EXCEPTION 'Step 4A acceptance identity is immutable';
    END IF;

    IF OLD.acceptance_request_id IS NOT NULL
       AND NEW.acceptance_request_id IS DISTINCT FROM OLD.acceptance_request_id THEN
      RAISE EXCEPTION 'Step 4A acceptance request binding is immutable';
    END IF;
    IF OLD.release_candidate_id IS NOT NULL
       AND NEW.release_candidate_id IS DISTINCT FROM OLD.release_candidate_id THEN
      RAISE EXCEPTION 'Step 4A release candidate binding is immutable';
    END IF;
    IF OLD.deployment_plan_id IS NOT NULL
       AND NEW.deployment_plan_id IS DISTINCT FROM OLD.deployment_plan_id THEN
      RAISE EXCEPTION 'Step 4A deployment plan binding is immutable';
    END IF;
    IF OLD.execution_id IS NOT NULL
       AND NEW.execution_id IS DISTINCT FROM OLD.execution_id THEN
      RAISE EXCEPTION 'Step 4A execution binding is immutable';
    END IF;
    IF OLD.candidate_vercel_deployment_id IS NOT NULL
       AND NEW.candidate_vercel_deployment_id IS DISTINCT FROM OLD.candidate_vercel_deployment_id THEN
      RAISE EXCEPTION 'Step 4A candidate binding is immutable';
    END IF;
    IF OLD.human_decision_id IS NOT NULL
       AND NEW.human_decision_id IS DISTINCT FROM OLD.human_decision_id THEN
      RAISE EXCEPTION 'Step 4A human decision binding is immutable';
    END IF;
  END IF;

  NEW.updated_at:=now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vercel_prepare_acceptance_guard
  ON vercel_prepare_acceptance_runs;
CREATE TRIGGER trg_vercel_prepare_acceptance_guard
BEFORE INSERT OR UPDATE ON vercel_prepare_acceptance_runs
FOR EACH ROW EXECUTE FUNCTION enforce_vercel_prepare_acceptance_guard();

COMMENT ON TABLE vercel_prepare_acceptance_runs IS
  'Preview-only Step 4A sacrificial Vercel PREPARE acceptance evidence. Promotion/rollback remain forbidden.';
