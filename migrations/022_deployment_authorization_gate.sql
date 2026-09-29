-- Deployment Authorization / Controlled Production Release v0.1
-- Authorization only. No production deployment executor is enabled by this schema.

CREATE TABLE IF NOT EXISTS deployment_plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  release_candidate_id uuid NOT NULL UNIQUE
    REFERENCES release_candidates(id) ON DELETE RESTRICT,
  release_decision_id uuid NOT NULL
    REFERENCES release_decisions(id) ON DELETE RESTRICT,
  review_package_id uuid NOT NULL
    REFERENCES release_review_packages(id) ON DELETE RESTRICT,

  plan_status text NOT NULL DEFAULT 'PENDING_AUTHORIZATION'
    CHECK (plan_status IN (
      'PENDING_AUTHORIZATION',
      'AUTHORIZED_FOR_DEPLOYMENT',
      'DEPLOYMENT_REJECTED'
    )),

  target_provider text NOT NULL
    CHECK (target_provider='VERCEL'),
  target_environment text NOT NULL
    CHECK (target_environment='production'),
  target_project_id text NOT NULL,
  target_team_id text,

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

  plan_sha256 text NOT NULL UNIQUE,
  execution_enabled boolean NOT NULL DEFAULT false
    CHECK (execution_enabled=false),

  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  authorized_at timestamptz,
  rejected_at timestamptz
);

CREATE TABLE IF NOT EXISTS deployment_authorization_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  deployment_plan_id uuid NOT NULL
    REFERENCES deployment_plans(id) ON DELETE RESTRICT,
  decision text NOT NULL
    CHECK (decision IN ('AUTHORIZE','REJECT')),
  reason text NOT NULL,
  actor text NOT NULL,
  plan_sha256 text NOT NULL,
  decided_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_deployment_authorization_terminal_decision
  ON deployment_authorization_decisions(deployment_plan_id);

CREATE TABLE IF NOT EXISTS deployment_authorization_blocks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  deployment_plan_id uuid NOT NULL
    REFERENCES deployment_plans(id) ON DELETE RESTRICT,
  actor text NOT NULL,
  reason text NOT NULL,
  blocking_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  integrity_status text NOT NULL,
  current_manifest_root_sha256 text,
  current_chain_head_sha256 text,
  blocked_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deployment_authorization_blocks_plan
  ON deployment_authorization_blocks(deployment_plan_id,blocked_at DESC);

CREATE OR REPLACE FUNCTION enforce_deployment_plan_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  release_ok boolean := false;
BEGIN
  IF NEW.execution_enabled THEN
    RAISE EXCEPTION 'Deployment authorization never enables execution';
  END IF;

  SELECT EXISTS (
    SELECT 1
    FROM release_candidates rc
    JOIN release_decisions rd
      ON rd.id=NEW.release_decision_id
     AND rd.release_candidate_id=rc.id
    JOIN release_review_packages rrp
      ON rrp.id=NEW.review_package_id
     AND rrp.release_candidate_id=rc.id
    WHERE rc.id=NEW.release_candidate_id
      AND rc.release_status='RELEASE_APPROVED'
      AND rc.deployment_enabled=false
      AND rc.archived_at IS NULL
      AND rd.decision='APPROVE'
      AND rd.candidate_status='RELEASE_APPROVED'
      AND rd.review_package_id=rrp.id
      AND rd.review_package_sha256=NEW.review_package_sha256
      AND rd.source_tree_sha256=NEW.source_tree_sha256
      AND rrp.package_status='GENERATED'
      AND rrp.content_snapshot_complete=true
      AND rrp.package_sha256=NEW.review_package_sha256
      AND rrp.source_tree_sha256=NEW.source_tree_sha256
  ) INTO release_ok;

  IF NOT release_ok THEN
    RAISE EXCEPTION 'Deployment plan requires current approved release binding';
  END IF;

  IF TG_OP='UPDATE' THEN
    IF OLD.plan_status IN ('AUTHORIZED_FOR_DEPLOYMENT','DEPLOYMENT_REJECTED')
       AND NEW.plan_status<>OLD.plan_status THEN
      RAISE EXCEPTION 'Terminal deployment authorization is immutable';
    END IF;

    IF NEW.release_candidate_id<>OLD.release_candidate_id
       OR NEW.release_decision_id<>OLD.release_decision_id
       OR NEW.review_package_id<>OLD.review_package_id
       OR NEW.target_provider<>OLD.target_provider
       OR NEW.target_environment<>OLD.target_environment
       OR NEW.target_project_id<>OLD.target_project_id
       OR NEW.target_team_id IS DISTINCT FROM OLD.target_team_id
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
       OR NEW.plan_sha256<>OLD.plan_sha256
       OR NEW.created_by<>OLD.created_by
       OR NEW.created_at<>OLD.created_at THEN
      RAISE EXCEPTION 'Deployment plan provenance snapshot is immutable';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_deployment_plan_guard ON deployment_plans;
CREATE TRIGGER trg_deployment_plan_guard
BEFORE INSERT OR UPDATE ON deployment_plans
FOR EACH ROW EXECUTE FUNCTION enforce_deployment_plan_guard();

CREATE OR REPLACE FUNCTION enforce_deployment_authorization_decision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  plan_row record;
BEGIN
  SELECT * INTO plan_row
  FROM deployment_plans
  WHERE id=NEW.deployment_plan_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Deployment plan not found';
  END IF;

  IF plan_row.plan_status<>'PENDING_AUTHORIZATION' THEN
    RAISE EXCEPTION 'Deployment authorization decision is already terminal';
  END IF;

  IF NEW.plan_sha256<>plan_row.plan_sha256 THEN
    RAISE EXCEPTION 'Deployment authorization plan SHA-256 mismatch';
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_deployment_authorization_decision
  ON deployment_authorization_decisions;
CREATE TRIGGER trg_deployment_authorization_decision
BEFORE INSERT ON deployment_authorization_decisions
FOR EACH ROW EXECUTE FUNCTION enforce_deployment_authorization_decision();

COMMENT ON TABLE deployment_plans IS
  'Immutable deployment authorization snapshots. v0.1 never executes production deployment.';
COMMENT ON TABLE deployment_authorization_decisions IS
  'Second human gate after RELEASE_APPROVED; authorization does not enable deployment execution.';
