CREATE TABLE IF NOT EXISTS shrimp_animation_publish_targets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  target_key text NOT NULL UNIQUE,
  platform text NOT NULL,
  display_name text NOT NULL,
  account_reference text,
  metadata_constraints jsonb NOT NULL DEFAULT '{}'::jsonb,
  target_status text NOT NULL DEFAULT 'ACTIVE',
  execution_enabled boolean NOT NULL DEFAULT false,
  external_publish_enabled boolean NOT NULL DEFAULT false,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (target_key ~ '^[A-Za-z0-9_.-]{3,120}$'),
  CHECK (platform IN ('BILIBILI','YOUTUBE','CUSTOM')),
  CHECK (char_length(display_name) >= 1),
  CHECK (jsonb_typeof(metadata_constraints)='object'),
  CHECK (target_status IN ('ACTIVE','INACTIVE')),
  CHECK (execution_enabled=false),
  CHECK (external_publish_enabled=false)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_publish_targets_platform
  ON shrimp_animation_publish_targets(platform,target_status);

CREATE TABLE IF NOT EXISTS shrimp_animation_publish_plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  review_decision_id uuid NOT NULL
    REFERENCES shrimp_animation_review_decisions(id) ON DELETE RESTRICT,
  target_id uuid NOT NULL
    REFERENCES shrimp_animation_publish_targets(id) ON DELETE RESTRICT,
  platform text NOT NULL,
  target_key text NOT NULL,
  review_decision_sha256 char(64) NOT NULL,
  episode_bundle_sha256 char(64) NOT NULL,
  release_review_package_sha256 char(64) NOT NULL,
  target_snapshot_sha256 char(64) NOT NULL,
  publish_metadata jsonb NOT NULL,
  dry_run_snapshot jsonb NOT NULL,
  dry_run_sha256 char(64) NOT NULL,
  dry_run_status text NOT NULL DEFAULT 'VERIFIED',
  plan_payload jsonb NOT NULL,
  plan_sha256 char(64) NOT NULL,
  plan_status text NOT NULL DEFAULT 'PENDING_AUTHORIZATION',
  execution_enabled boolean NOT NULL DEFAULT false,
  publish_performed boolean NOT NULL DEFAULT false,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  authorized_at timestamptz,
  rejected_at timestamptz,
  superseded_at timestamptz,
  CHECK (platform IN ('BILIBILI','YOUTUBE','CUSTOM')),
  CHECK (char_length(review_decision_sha256)=64),
  CHECK (char_length(episode_bundle_sha256)=64),
  CHECK (char_length(release_review_package_sha256)=64),
  CHECK (char_length(target_snapshot_sha256)=64),
  CHECK (jsonb_typeof(publish_metadata)='object'),
  CHECK (jsonb_typeof(dry_run_snapshot)='object'),
  CHECK (char_length(dry_run_sha256)=64),
  CHECK (dry_run_status IN ('VERIFIED','FAILED')),
  CHECK (jsonb_typeof(plan_payload)='object'),
  CHECK (char_length(plan_sha256)=64),
  CHECK (
    plan_status IN (
      'PENDING_AUTHORIZATION',
      'PUBLISH_AUTHORIZED',
      'PUBLISH_REJECTED',
      'STALE'
    )
  ),
  CHECK (execution_enabled=false),
  CHECK (publish_performed=false)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_publish_current_plan
  ON shrimp_animation_publish_plans(provider_job_id,target_id)
  WHERE plan_status<>'STALE';

CREATE INDEX IF NOT EXISTS idx_shrimp_publish_plans_job
  ON shrimp_animation_publish_plans(provider_job_id,created_at DESC);

CREATE INDEX IF NOT EXISTS idx_shrimp_publish_plans_target
  ON shrimp_animation_publish_plans(target_id,created_at DESC);

CREATE TABLE IF NOT EXISTS shrimp_animation_publish_authorization_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  publish_plan_id uuid NOT NULL
    REFERENCES shrimp_animation_publish_plans(id) ON DELETE CASCADE,
  decision text NOT NULL,
  reason text NOT NULL,
  actor text NOT NULL,
  plan_sha256 char(64) NOT NULL,
  dry_run_sha256 char(64) NOT NULL,
  decision_sha256 char(64) NOT NULL,
  decision_status text NOT NULL DEFAULT 'CURRENT',
  decided_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CHECK (decision IN ('AUTHORIZE','REJECT')),
  CHECK (char_length(reason)>=3),
  CHECK (char_length(actor)>=1),
  CHECK (char_length(plan_sha256)=64),
  CHECK (char_length(dry_run_sha256)=64),
  CHECK (char_length(decision_sha256)=64),
  CHECK (decision_status IN ('CURRENT','STALE'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_publish_current_authorization
  ON shrimp_animation_publish_authorization_decisions(publish_plan_id)
  WHERE decision_status='CURRENT';

CREATE TABLE IF NOT EXISTS shrimp_animation_publish_authorization_blocks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  publish_plan_id uuid NOT NULL
    REFERENCES shrimp_animation_publish_plans(id) ON DELETE CASCADE,
  actor text NOT NULL,
  reason text NOT NULL,
  blocking_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  current_review_decision_sha256 char(64),
  current_target_snapshot_sha256 char(64),
  current_dry_run_sha256 char(64),
  blocked_at timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(blocking_reasons)='array')
);

CREATE OR REPLACE FUNCTION prevent_shrimp_publish_plan_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.provider_job_id IS DISTINCT FROM OLD.provider_job_id
     OR NEW.review_decision_id IS DISTINCT FROM OLD.review_decision_id
     OR NEW.target_id IS DISTINCT FROM OLD.target_id
     OR NEW.platform IS DISTINCT FROM OLD.platform
     OR NEW.target_key IS DISTINCT FROM OLD.target_key
     OR NEW.review_decision_sha256 IS DISTINCT FROM OLD.review_decision_sha256
     OR NEW.episode_bundle_sha256 IS DISTINCT FROM OLD.episode_bundle_sha256
     OR NEW.release_review_package_sha256 IS DISTINCT FROM OLD.release_review_package_sha256
     OR NEW.target_snapshot_sha256 IS DISTINCT FROM OLD.target_snapshot_sha256
     OR NEW.publish_metadata IS DISTINCT FROM OLD.publish_metadata
     OR NEW.dry_run_snapshot IS DISTINCT FROM OLD.dry_run_snapshot
     OR NEW.dry_run_sha256 IS DISTINCT FROM OLD.dry_run_sha256
     OR NEW.dry_run_status IS DISTINCT FROM OLD.dry_run_status
     OR NEW.plan_payload IS DISTINCT FROM OLD.plan_payload
     OR NEW.plan_sha256 IS DISTINCT FROM OLD.plan_sha256
     OR NEW.execution_enabled IS DISTINCT FROM OLD.execution_enabled
     OR NEW.publish_performed IS DISTINCT FROM OLD.publish_performed
     OR NEW.created_by IS DISTINCT FROM OLD.created_by
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
  THEN
    RAISE EXCEPTION 'Shrimp Publisher Plan identity/content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_publish_plan_immutable
  ON shrimp_animation_publish_plans;
CREATE TRIGGER trg_shrimp_publish_plan_immutable
BEFORE UPDATE ON shrimp_animation_publish_plans
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_publish_plan_mutation();

CREATE OR REPLACE FUNCTION prevent_shrimp_publish_authorization_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.publish_plan_id IS DISTINCT FROM OLD.publish_plan_id
     OR NEW.decision IS DISTINCT FROM OLD.decision
     OR NEW.reason IS DISTINCT FROM OLD.reason
     OR NEW.actor IS DISTINCT FROM OLD.actor
     OR NEW.plan_sha256 IS DISTINCT FROM OLD.plan_sha256
     OR NEW.dry_run_sha256 IS DISTINCT FROM OLD.dry_run_sha256
     OR NEW.decision_sha256 IS DISTINCT FROM OLD.decision_sha256
     OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
  THEN
    RAISE EXCEPTION 'Shrimp publish authorization decision identity/content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_publish_authorization_immutable
  ON shrimp_animation_publish_authorization_decisions;
CREATE TRIGGER trg_shrimp_publish_authorization_immutable
BEFORE UPDATE ON shrimp_animation_publish_authorization_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_publish_authorization_mutation();

CREATE OR REPLACE FUNCTION stale_shrimp_publish_plans_when_review_stales()
RETURNS trigger AS $$
BEGIN
  IF OLD.decision_status='CURRENT'
     AND NEW.decision_status='STALE'
  THEN
    UPDATE shrimp_animation_publish_plans
    SET plan_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE review_decision_id=NEW.id
      AND plan_status<>'STALE';

    UPDATE shrimp_animation_publish_authorization_decisions
    SET decision_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE publish_plan_id IN (
      SELECT id
      FROM shrimp_animation_publish_plans
      WHERE review_decision_id=NEW.id
    )
      AND decision_status='CURRENT';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_publish_plans_stale_on_review
  ON shrimp_animation_review_decisions;
CREATE TRIGGER trg_shrimp_publish_plans_stale_on_review
AFTER UPDATE ON shrimp_animation_review_decisions
FOR EACH ROW EXECUTE FUNCTION stale_shrimp_publish_plans_when_review_stales();

CREATE OR REPLACE FUNCTION stale_shrimp_publish_plans_when_target_changes()
RETURNS trigger AS $$
BEGIN
  IF NEW.platform IS DISTINCT FROM OLD.platform
     OR NEW.display_name IS DISTINCT FROM OLD.display_name
     OR NEW.account_reference IS DISTINCT FROM OLD.account_reference
     OR NEW.metadata_constraints IS DISTINCT FROM OLD.metadata_constraints
     OR NEW.target_status IS DISTINCT FROM OLD.target_status
  THEN
    UPDATE shrimp_animation_publish_plans
    SET plan_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE target_id=NEW.id
      AND plan_status<>'STALE';

    UPDATE shrimp_animation_publish_authorization_decisions
    SET decision_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE publish_plan_id IN (
      SELECT id
      FROM shrimp_animation_publish_plans
      WHERE target_id=NEW.id
    )
      AND decision_status='CURRENT';
  END IF;

  NEW.updated_at=now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_publish_target_change
  ON shrimp_animation_publish_targets;
CREATE TRIGGER trg_shrimp_publish_target_change
BEFORE UPDATE ON shrimp_animation_publish_targets
FOR EACH ROW EXECUTE FUNCTION stale_shrimp_publish_plans_when_target_changes();
