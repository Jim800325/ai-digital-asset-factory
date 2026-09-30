-- Controlled Production Release Executor v0.1 — Step 4
-- Vercel PREPARE adapter for sacrificial Preview acceptance only.
-- No promotion, alias/domain assignment, rollback, or real Production project write.

ALTER TABLE production_release_executions
  DROP CONSTRAINT IF EXISTS production_release_executions_execution_status_check;

ALTER TABLE production_release_executions
  ADD CONSTRAINT production_release_executions_execution_status_check
  CHECK (execution_status IN (
    'SNAPSHOT_CREATED','PREPARING','PREPARE_UNKNOWN','READY_FOR_PROMOTION',
    'ABORTED','PREPARE_FAILED','PROMOTION_REQUESTED',
    'PROMOTION_UNKNOWN','PRODUCTION_ACTIVE','ROLLBACK_REQUIRED',
    'ROLLBACK_REQUESTED','ROLLBACK_UNKNOWN','ROLLED_BACK'
  ));

ALTER TABLE production_release_executions
  DROP CONSTRAINT IF EXISTS production_release_executions_executor_adapter_check;

ALTER TABLE production_release_executions
  ADD CONSTRAINT production_release_executions_executor_adapter_check
  CHECK (executor_adapter IN ('MOCK','VERCEL_CONTROLLED_EXECUTOR'));

ALTER TABLE production_release_executions
  ADD COLUMN IF NOT EXISTS prepare_request_sha256 text,
  ADD COLUMN IF NOT EXISTS prepare_provider_deployment_id text,
  ADD COLUMN IF NOT EXISTS prepare_provider_state text,
  ADD COLUMN IF NOT EXISTS prepare_outcome text NOT NULL DEFAULT 'NOT_ATTEMPTED'
    CHECK (prepare_outcome IN (
      'NOT_ATTEMPTED','REQUESTED','ACCEPTED','AMBIGUOUS',
      'RECONCILED_PENDING','RECONCILED_READY','RECONCILED_FAILED',
      'REJECTED'
    )),
  ADD COLUMN IF NOT EXISTS prepare_write_count integer NOT NULL DEFAULT 0
    CHECK (prepare_write_count BETWEEN 0 AND 1),
  ADD COLUMN IF NOT EXISTS prepare_provider_result_sha256 text,
  ADD COLUMN IF NOT EXISTS prepare_last_error_type text,
  ADD COLUMN IF NOT EXISTS prepare_last_error_sha256 text,
  ADD COLUMN IF NOT EXISTS prepare_attempted_at timestamptz,
  ADD COLUMN IF NOT EXISTS prepare_reconciled_at timestamptz;

ALTER TABLE production_release_executions
  ADD CONSTRAINT production_release_executions_prepare_request_sha_check
  CHECK (
    prepare_request_sha256 IS NULL
    OR length(prepare_request_sha256)=64
  );

ALTER TABLE production_release_executions
  ADD CONSTRAINT production_release_executions_prepare_result_sha_check
  CHECK (
    prepare_provider_result_sha256 IS NULL
    OR length(prepare_provider_result_sha256)=64
  );

ALTER TABLE production_release_executions
  ADD CONSTRAINT production_release_executions_prepare_error_sha_check
  CHECK (
    prepare_last_error_sha256 IS NULL
    OR length(prepare_last_error_sha256)=64
  );

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
    RAISE EXCEPTION 'Controlled Production Release Executor remains explicitly gated';
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
    IF NEW.prepare_write_count<>0
       OR NEW.prepare_outcome<>'NOT_ATTEMPTED' THEN
      RAISE EXCEPTION 'Production execution PREPARE must begin unattempted';
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

    IF OLD.prepare_request_sha256 IS NOT NULL
       AND NEW.prepare_request_sha256 IS DISTINCT FROM OLD.prepare_request_sha256 THEN
      RAISE EXCEPTION 'PREPARE request SHA-256 is immutable';
    END IF;
    IF OLD.prepare_provider_deployment_id IS NOT NULL
       AND NEW.prepare_provider_deployment_id IS DISTINCT FROM OLD.prepare_provider_deployment_id THEN
      RAISE EXCEPTION 'PREPARE provider deployment ID is immutable';
    END IF;
    IF NEW.prepare_write_count < OLD.prepare_write_count
       OR NEW.prepare_write_count > 1 THEN
      RAISE EXCEPTION 'PREPARE provider write count is monotonic and bounded to one';
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
        OR (OLD.execution_status='PREPARING' AND NEW.execution_status IN ('PREPARE_UNKNOWN','READY_FOR_PROMOTION','PREPARE_FAILED'))
        OR (OLD.execution_status='PREPARE_UNKNOWN' AND NEW.execution_status IN ('READY_FOR_PROMOTION','PREPARE_FAILED'))
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

COMMENT ON COLUMN production_release_executions.prepare_write_count IS
  'Exactly-once PREPARE mutation budget. A value of 1 forbids any automatic replay.';
COMMENT ON COLUMN production_release_executions.prepare_provider_deployment_id IS
  'Deterministic client-supplied Vercel deployment ID used for read-only reconciliation after ambiguous writes.';
COMMENT ON COLUMN production_release_executions.prepare_outcome IS
  'Provider PREPARE outcome. AMBIGUOUS requires reconciliation reads and forbids replay.';
