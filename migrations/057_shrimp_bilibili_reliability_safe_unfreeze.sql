-- Step 10B.16 — Safe Unfreeze + Recovery Evidence + Two-Person Restore Approval

ALTER TABLE shrimp_bilibili_reliability_policy_control_events
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_reliability_policy_control_events_event_type_check;
ALTER TABLE shrimp_bilibili_reliability_policy_control_events
  ADD CONSTRAINT shrimp_bilibili_reliability_policy_control_events_event_type_check
  CHECK (event_type IN ('PLAN_APPLIED','SAFE_UNFREEZE_APPLIED'));

ALTER TABLE shrimp_bilibili_reliability_policy_control_events
  ADD COLUMN IF NOT EXISTS restore_plan_id uuid;

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_restore_plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  restore_key text NOT NULL UNIQUE,
  plan_status text NOT NULL DEFAULT 'PENDING_FIRST_APPROVAL',
  source_control_snapshot jsonb NOT NULL,
  proposed_control_snapshot jsonb NOT NULL,
  recovery_evidence_snapshot jsonb NOT NULL,
  exact_change_set jsonb NOT NULL,
  dry_run_diff jsonb NOT NULL,
  source_control_sha256 char(64) NOT NULL,
  proposed_control_sha256 char(64) NOT NULL,
  recovery_evidence_sha256 char(64) NOT NULL,
  plan_sha256 char(64) NOT NULL UNIQUE,
  dry_run_sha256 char(64) NOT NULL UNIQUE,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  first_approved_at timestamptz,
  applied_at timestamptz,
  stale_at timestamptz,
  CHECK (
    plan_status IN (
      'PENDING_FIRST_APPROVAL','PENDING_SECOND_APPROVAL',
      'APPLIED','REJECTED','STALE'
    )
  ),
  CHECK (jsonb_typeof(source_control_snapshot)='object'),
  CHECK (jsonb_typeof(proposed_control_snapshot)='object'),
  CHECK (jsonb_typeof(recovery_evidence_snapshot)='object'),
  CHECK (jsonb_typeof(exact_change_set)='array'),
  CHECK (jsonb_typeof(dry_run_diff)='object'),
  CHECK (char_length(source_control_sha256)=64),
  CHECK (char_length(proposed_control_sha256)=64),
  CHECK (char_length(recovery_evidence_sha256)=64),
  CHECK (char_length(plan_sha256)=64),
  CHECK (char_length(dry_run_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_pending_restore_plan
  ON shrimp_bilibili_reliability_restore_plans((1))
  WHERE plan_status IN ('PENDING_FIRST_APPROVAL','PENDING_SECOND_APPROVAL');

ALTER TABLE shrimp_bilibili_reliability_policy_control_events
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_reliability_policy_control_events_restore_plan_id_fkey;
ALTER TABLE shrimp_bilibili_reliability_policy_control_events
  ADD CONSTRAINT shrimp_bilibili_reliability_policy_control_events_restore_plan_id_fkey
  FOREIGN KEY (restore_plan_id)
  REFERENCES shrimp_bilibili_reliability_restore_plans(id)
  ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION prevent_bilibili_restore_plan_evidence_mutation()
RETURNS trigger AS $restore_plan$
BEGIN
  IF OLD.plan_status IN ('PENDING_FIRST_APPROVAL','PENDING_SECOND_APPROVAL')
     AND NEW.plan_status IN (
       'PENDING_SECOND_APPROVAL','APPLIED','REJECTED','STALE'
     )
     AND NEW.source_control_snapshot=OLD.source_control_snapshot
     AND NEW.proposed_control_snapshot=OLD.proposed_control_snapshot
     AND NEW.recovery_evidence_snapshot=OLD.recovery_evidence_snapshot
     AND NEW.exact_change_set=OLD.exact_change_set
     AND NEW.dry_run_diff=OLD.dry_run_diff
     AND NEW.source_control_sha256=OLD.source_control_sha256
     AND NEW.proposed_control_sha256=OLD.proposed_control_sha256
     AND NEW.recovery_evidence_sha256=OLD.recovery_evidence_sha256
     AND NEW.plan_sha256=OLD.plan_sha256
     AND NEW.dry_run_sha256=OLD.dry_run_sha256
  THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Reliability restore plan evidence is immutable';
END;
$restore_plan$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_restore_plan_evidence_mutation
  ON shrimp_bilibili_reliability_restore_plans;
CREATE TRIGGER trg_prevent_bilibili_restore_plan_evidence_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_reliability_restore_plans
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_restore_plan_evidence_mutation();

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_restore_approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL
    REFERENCES shrimp_bilibili_reliability_restore_plans(id) ON DELETE RESTRICT,
  approval_stage text NOT NULL,
  decision text NOT NULL,
  reason text NOT NULL,
  actor text NOT NULL,
  plan_sha256 char(64) NOT NULL,
  dry_run_sha256 char(64) NOT NULL,
  recovery_evidence_sha256 char(64) NOT NULL,
  approval_sha256 char(64) NOT NULL UNIQUE,
  decided_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(plan_id,approval_stage),
  CHECK (approval_stage IN ('FIRST_APPROVAL','SECOND_APPLY')),
  CHECK (decision IN ('APPROVE','REJECT')),
  CHECK (char_length(reason)>=3),
  CHECK (char_length(plan_sha256)=64),
  CHECK (char_length(dry_run_sha256)=64),
  CHECK (char_length(recovery_evidence_sha256)=64),
  CHECK (char_length(approval_sha256)=64)
);

CREATE OR REPLACE FUNCTION prevent_bilibili_restore_approval_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Reliability restore approval is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_restore_approval_mutation
  ON shrimp_bilibili_reliability_restore_approvals;
CREATE TRIGGER trg_prevent_bilibili_restore_approval_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_reliability_restore_approvals
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_restore_approval_mutation();
