-- Controlled Production Release Executor v0.1 — Step 1
-- Immutable execution snapshot + state machine only.
-- Real Production provider mutations remain disabled.

CREATE TABLE IF NOT EXISTS production_release_executions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  deployment_plan_id uuid NOT NULL UNIQUE
    REFERENCES deployment_plans(id) ON DELETE RESTRICT,
  deployment_authorization_decision_id uuid NOT NULL
    REFERENCES deployment_authorization_decisions(id) ON DELETE RESTRICT,
  release_candidate_id uuid NOT NULL
    REFERENCES release_candidates(id) ON DELETE RESTRICT,
  review_package_id uuid NOT NULL
    REFERENCES release_review_packages(id) ON DELETE RESTRICT,

  execution_status text NOT NULL DEFAULT 'SNAPSHOT_CREATED'
    CHECK (execution_status IN (
      'SNAPSHOT_CREATED','PREPARING','READY_FOR_PROMOTION',
      'ABORTED','PREPARE_FAILED','PROMOTION_REQUESTED',
      'PROMOTION_UNKNOWN','PRODUCTION_ACTIVE','ROLLBACK_REQUIRED',
      'ROLLBACK_REQUESTED','ROLLBACK_UNKNOWN','ROLLED_BACK'
    )),

  executor_adapter text NOT NULL DEFAULT 'MOCK'
    CHECK (executor_adapter='MOCK'),
  target_provider text NOT NULL CHECK (target_provider='VERCEL'),
  target_environment text NOT NULL CHECK (target_environment='production'),
  target_project_id text NOT NULL,
  target_team_id text,

  plan_sha256 text NOT NULL,
  review_package_sha256 text NOT NULL,
  source_tree_sha256 text NOT NULL,
  acceptance_provenance_tree_sha256 text NOT NULL,
  live_acceptance_audit_id text NOT NULL,
  audit_evidence_sha256 text NOT NULL,
  audit_chain_sha256 text NOT NULL,
  manifest_root_sha256 text NOT NULL,
  chain_head_sha256 text NOT NULL,
  source_commit text NOT NULL,
  deployment_source_commit text NOT NULL,
  source_vercel_deployment_id text NOT NULL,

  execution_bundle jsonb NOT NULL,
  execution_bundle_sha256 text NOT NULL,
  execution_sha256 text NOT NULL UNIQUE,

  candidate_vercel_deployment_id text,
  candidate_vercel_url text,
  previous_production_deployment_id text,
  production_vercel_deployment_id text,

  production_execution_enabled boolean NOT NULL DEFAULT false
    CHECK (production_execution_enabled=false),
  automatic_execution boolean NOT NULL DEFAULT false
    CHECK (automatic_execution=false),
  automatic_promotion boolean NOT NULL DEFAULT false
    CHECK (automatic_promotion=false),

  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  prepared_at timestamptz,
  promoted_at timestamptz,
  verified_at timestamptz,
  rolled_back_at timestamptz,

  CHECK (length(plan_sha256)=64),
  CHECK (length(review_package_sha256)=64),
  CHECK (length(source_tree_sha256)=64),
  CHECK (length(acceptance_provenance_tree_sha256)=64),
  CHECK (length(audit_evidence_sha256)=64),
  CHECK (length(audit_chain_sha256)=64),
  CHECK (length(manifest_root_sha256)=64),
  CHECK (length(chain_head_sha256)=64),
  CHECK (length(execution_bundle_sha256)=64),
  CHECK (length(execution_sha256)=64)
);

CREATE TABLE IF NOT EXISTS production_release_execution_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id uuid NOT NULL
    REFERENCES production_release_executions(id) ON DELETE RESTRICT,
  decision text NOT NULL CHECK (decision IN ('PROMOTE','ABORT')),
  reason text NOT NULL,
  actor text NOT NULL,
  execution_sha256 text NOT NULL,
  decided_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(execution_id)
);

CREATE TABLE IF NOT EXISTS production_release_execution_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id uuid NOT NULL
    REFERENCES production_release_executions(id) ON DELETE RESTRICT,
  event_type text NOT NULL CHECK (event_type IN (
    'EXECUTION_SNAPSHOT_CREATED','PREPARE_REQUESTED',
    'CANDIDATE_CREATED','CANDIDATE_READY','CANDIDATE_VERIFIED',
    'PROMOTE_REQUESTED','PROMOTE_PROVIDER_ACCEPTED',
    'PRODUCTION_POINTER_VERIFIED','PRODUCTION_HEALTH_VERIFIED',
    'RECONCILIATION_REQUIRED','ROLLBACK_REQUESTED',
    'ROLLBACK_PROVIDER_ACCEPTED','ROLLBACK_VERIFIED','FAILURE'
  )),
  previous_status text,
  next_status text,
  actor text NOT NULL,
  provider_result_sha256 text,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    provider_result_sha256 IS NULL
    OR length(provider_result_sha256)=64
  )
);

CREATE INDEX IF NOT EXISTS idx_production_release_execution_events
  ON production_release_execution_events(execution_id,created_at,id);

CREATE OR REPLACE FUNCTION enforce_production_release_execution_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  source_ok boolean := false;
BEGIN
  IF NEW.production_execution_enabled
     OR NEW.automatic_execution
     OR NEW.automatic_promotion THEN
    RAISE EXCEPTION 'Controlled Production Release Executor Step 1 remains disabled';
  END IF;

  SELECT EXISTS (
    SELECT 1
    FROM deployment_plans dp
    JOIN deployment_authorization_decisions dad
      ON dad.id=NEW.deployment_authorization_decision_id
     AND dad.deployment_plan_id=dp.id
    JOIN release_candidates rc
      ON rc.id=NEW.release_candidate_id
     AND rc.id=dp.release_candidate_id
    JOIN release_review_packages rrp
      ON rrp.id=NEW.review_package_id
     AND rrp.id=dp.review_package_id
     AND rrp.release_candidate_id=rc.id
    WHERE dp.id=NEW.deployment_plan_id
      AND dp.plan_status='AUTHORIZED_FOR_DEPLOYMENT'
      AND dp.execution_enabled=false
      AND dad.decision='AUTHORIZE'
      AND dad.plan_sha256=dp.plan_sha256
      AND rc.release_status='RELEASE_APPROVED'
      AND rc.deployment_enabled=false
      AND rc.archived_at IS NULL
      AND rrp.package_status='GENERATED'
      AND rrp.content_snapshot_complete=true
      AND NEW.plan_sha256=dp.plan_sha256
      AND NEW.review_package_sha256=dp.review_package_sha256
      AND NEW.source_tree_sha256=dp.source_tree_sha256
      AND NEW.acceptance_provenance_tree_sha256=dp.acceptance_provenance_tree_sha256
      AND NEW.live_acceptance_audit_id=dp.live_acceptance_audit_id
      AND NEW.audit_evidence_sha256=dp.audit_evidence_sha256
      AND NEW.audit_chain_sha256=dp.audit_chain_sha256
      AND NEW.manifest_root_sha256=dp.manifest_root_sha256
      AND NEW.chain_head_sha256=dp.chain_head_sha256
      AND NEW.source_commit=dp.source_commit
      AND NEW.deployment_source_commit=dp.deployment_source_commit
      AND NEW.source_vercel_deployment_id=dp.source_vercel_deployment_id
      AND NEW.target_provider=dp.target_provider
      AND NEW.target_environment=dp.target_environment
      AND NEW.target_project_id=dp.target_project_id
      AND NEW.target_team_id IS NOT DISTINCT FROM dp.target_team_id
      AND NEW.review_package_sha256=rrp.package_sha256
      AND NEW.source_tree_sha256=rrp.source_tree_sha256
  ) INTO source_ok;

  IF NOT source_ok THEN
    RAISE EXCEPTION 'Production execution requires current authorized immutable release binding';
  END IF;

  IF TG_OP='INSERT' THEN
    IF NEW.execution_status<>'SNAPSHOT_CREATED' THEN
      RAISE EXCEPTION 'Production execution must begin at SNAPSHOT_CREATED';
    END IF;
  ELSE
    IF NEW.deployment_plan_id<>OLD.deployment_plan_id
       OR NEW.deployment_authorization_decision_id<>OLD.deployment_authorization_decision_id
       OR NEW.release_candidate_id<>OLD.release_candidate_id
       OR NEW.review_package_id<>OLD.review_package_id
       OR NEW.executor_adapter<>OLD.executor_adapter
       OR NEW.target_provider<>OLD.target_provider
       OR NEW.target_environment<>OLD.target_environment
       OR NEW.target_project_id<>OLD.target_project_id
       OR NEW.target_team_id IS DISTINCT FROM OLD.target_team_id
       OR NEW.plan_sha256<>OLD.plan_sha256
       OR NEW.review_package_sha256<>OLD.review_package_sha256
       OR NEW.source_tree_sha256<>OLD.source_tree_sha256
       OR NEW.acceptance_provenance_tree_sha256<>OLD.acceptance_provenance_tree_sha256
       OR NEW.live_acceptance_audit_id<>OLD.live_acceptance_audit_id
       OR NEW.audit_evidence_sha256<>OLD.audit_evidence_sha256
       OR NEW.audit_chain_sha256<>OLD.audit_chain_sha256
       OR NEW.manifest_root_sha256<>OLD.manifest_root_sha256
       OR NEW.chain_head_sha256<>OLD.chain_head_sha256
       OR NEW.source_commit<>OLD.source_commit
       OR NEW.deployment_source_commit<>OLD.deployment_source_commit
       OR NEW.source_vercel_deployment_id<>OLD.source_vercel_deployment_id
       OR NEW.execution_bundle<>OLD.execution_bundle
       OR NEW.execution_bundle_sha256<>OLD.execution_bundle_sha256
       OR NEW.execution_sha256<>OLD.execution_sha256
       OR NEW.created_by<>OLD.created_by
       OR NEW.created_at<>OLD.created_at THEN
      RAISE EXCEPTION 'Production execution snapshot is immutable';
    END IF;

    IF OLD.candidate_vercel_deployment_id IS NOT NULL
       AND NEW.candidate_vercel_deployment_id IS DISTINCT FROM OLD.candidate_vercel_deployment_id THEN
      RAISE EXCEPTION 'Prepared candidate deployment is immutable';
    END IF;
    IF OLD.candidate_vercel_url IS NOT NULL
       AND NEW.candidate_vercel_url IS DISTINCT FROM OLD.candidate_vercel_url THEN
      RAISE EXCEPTION 'Prepared candidate URL is immutable';
    END IF;
    IF OLD.previous_production_deployment_id IS NOT NULL
       AND NEW.previous_production_deployment_id IS DISTINCT FROM OLD.previous_production_deployment_id THEN
      RAISE EXCEPTION 'Captured rollback target is immutable';
    END IF;
    IF OLD.production_vercel_deployment_id IS NOT NULL
       AND NEW.production_vercel_deployment_id IS DISTINCT FROM OLD.production_vercel_deployment_id THEN
      RAISE EXCEPTION 'Production deployment pointer evidence is immutable';
    END IF;

    IF NEW.execution_status<>OLD.execution_status THEN
      IF NOT (
        (OLD.execution_status='SNAPSHOT_CREATED' AND NEW.execution_status IN ('PREPARING','ABORTED'))
        OR (OLD.execution_status='PREPARING' AND NEW.execution_status IN ('READY_FOR_PROMOTION','PREPARE_FAILED'))
        OR (OLD.execution_status='READY_FOR_PROMOTION' AND NEW.execution_status IN ('ABORTED','PROMOTION_REQUESTED'))
        OR (OLD.execution_status='PROMOTION_REQUESTED' AND NEW.execution_status IN ('PROMOTION_UNKNOWN','PRODUCTION_ACTIVE','ROLLBACK_REQUIRED'))
        OR (OLD.execution_status='PROMOTION_UNKNOWN' AND NEW.execution_status IN ('PRODUCTION_ACTIVE','ROLLBACK_REQUIRED'))
        OR (OLD.execution_status='PRODUCTION_ACTIVE' AND NEW.execution_status='ROLLBACK_REQUIRED')
        OR (OLD.execution_status='ROLLBACK_REQUIRED' AND NEW.execution_status='ROLLBACK_REQUESTED')
        OR (OLD.execution_status='ROLLBACK_REQUESTED' AND NEW.execution_status IN ('ROLLBACK_UNKNOWN','ROLLED_BACK'))
        OR (OLD.execution_status='ROLLBACK_UNKNOWN' AND NEW.execution_status='ROLLED_BACK')
      ) THEN
        RAISE EXCEPTION 'Invalid production release execution state transition';
      END IF;
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_production_release_execution_guard
  ON production_release_executions;
CREATE TRIGGER trg_production_release_execution_guard
BEFORE INSERT OR UPDATE ON production_release_executions
FOR EACH ROW EXECUTE FUNCTION enforce_production_release_execution_guard();

CREATE OR REPLACE FUNCTION enforce_production_release_execution_decision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  execution_row record;
BEGIN
  SELECT * INTO execution_row
  FROM production_release_executions
  WHERE id=NEW.execution_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Production release execution not found';
  END IF;
  IF NEW.execution_sha256<>execution_row.execution_sha256 THEN
    RAISE EXCEPTION 'Production execution SHA-256 mismatch';
  END IF;
  IF NEW.decision='PROMOTE'
     AND execution_row.execution_status<>'READY_FOR_PROMOTION' THEN
    RAISE EXCEPTION 'PROMOTE requires READY_FOR_PROMOTION';
  END IF;
  IF NEW.decision='ABORT'
     AND execution_row.execution_status NOT IN ('SNAPSHOT_CREATED','READY_FOR_PROMOTION') THEN
    RAISE EXCEPTION 'ABORT is not allowed from current execution state';
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_production_release_execution_decision
  ON production_release_execution_decisions;
CREATE TRIGGER trg_production_release_execution_decision
BEFORE INSERT ON production_release_execution_decisions
FOR EACH ROW EXECUTE FUNCTION enforce_production_release_execution_decision();

CREATE OR REPLACE FUNCTION protect_production_release_audit_rows()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'Production release execution audit rows are append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_production_release_decision_append_only
  ON production_release_execution_decisions;
CREATE TRIGGER trg_production_release_decision_append_only
BEFORE UPDATE OR DELETE ON production_release_execution_decisions
FOR EACH ROW EXECUTE FUNCTION protect_production_release_audit_rows();

DROP TRIGGER IF EXISTS trg_production_release_event_append_only
  ON production_release_execution_events;
CREATE TRIGGER trg_production_release_event_append_only
BEFORE UPDATE OR DELETE ON production_release_execution_events
FOR EACH ROW EXECUTE FUNCTION protect_production_release_audit_rows();

COMMENT ON TABLE production_release_executions IS
  'Step 1 immutable execution snapshots. Real Production execution remains disabled.';
COMMENT ON TABLE production_release_execution_decisions IS
  'Third human-gate persistence shape. No Production decision API exists in Step 1.';
COMMENT ON TABLE production_release_execution_events IS
  'Append-only execution state evidence. Step 1 provider adapter is MOCK only.';
