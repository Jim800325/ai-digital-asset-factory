-- Step 10B.15 — Controlled Reliability Policy Change Plan + Dry-Run + Second Human Apply Gate

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_policy_controls (
  control_key text PRIMARY KEY,
  automation_exposure text NOT NULL DEFAULT 'NORMAL',
  quota_multiplier_percent integer NOT NULL DEFAULT 100,
  new_reservation_allowed boolean NOT NULL DEFAULT true,
  control_version integer NOT NULL DEFAULT 1,
  source_plan_id uuid,
  updated_by text NOT NULL DEFAULT 'system',
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (control_key='GLOBAL'),
  CHECK (automation_exposure IN ('NORMAL','CAUTION','FROZEN')),
  CHECK (quota_multiplier_percent BETWEEN 0 AND 100),
  CHECK (control_version>=1)
);

INSERT INTO shrimp_bilibili_reliability_policy_controls(
  control_key,automation_exposure,quota_multiplier_percent,
  new_reservation_allowed,control_version,updated_by)
VALUES('GLOBAL','NORMAL',100,true,1,'migration-056')
ON CONFLICT (control_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_change_plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  intent_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_reliability_policy_intents(id) ON DELETE RESTRICT,
  review_id uuid NOT NULL
    REFERENCES shrimp_bilibili_reliability_governance_reviews(id) ON DELETE RESTRICT,
  decision_id uuid NOT NULL
    REFERENCES shrimp_bilibili_reliability_governance_decisions(id) ON DELETE RESTRICT,
  plan_status text NOT NULL DEFAULT 'PENDING_APPLY',
  current_control_snapshot jsonb NOT NULL,
  proposed_control_snapshot jsonb NOT NULL,
  exact_change_set jsonb NOT NULL,
  dry_run_diff jsonb NOT NULL,
  current_control_sha256 char(64) NOT NULL,
  proposed_control_sha256 char(64) NOT NULL,
  plan_sha256 char(64) NOT NULL UNIQUE,
  dry_run_sha256 char(64) NOT NULL UNIQUE,
  governance_evidence_sha256 char(64) NOT NULL,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  applied_at timestamptz,
  stale_at timestamptz,
  CHECK (plan_status IN ('PENDING_APPLY','APPLIED','STALE')),
  CHECK (jsonb_typeof(current_control_snapshot)='object'),
  CHECK (jsonb_typeof(proposed_control_snapshot)='object'),
  CHECK (jsonb_typeof(exact_change_set)='array'),
  CHECK (jsonb_typeof(dry_run_diff)='object'),
  CHECK (char_length(current_control_sha256)=64),
  CHECK (char_length(proposed_control_sha256)=64),
  CHECK (char_length(plan_sha256)=64),
  CHECK (char_length(dry_run_sha256)=64),
  CHECK (char_length(governance_evidence_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_pending_reliability_change_plan
  ON shrimp_bilibili_reliability_change_plans((1))
  WHERE plan_status='PENDING_APPLY';

ALTER TABLE shrimp_bilibili_reliability_policy_controls
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_reliability_policy_controls_source_plan_id_fkey;
ALTER TABLE shrimp_bilibili_reliability_policy_controls
  ADD CONSTRAINT shrimp_bilibili_reliability_policy_controls_source_plan_id_fkey
  FOREIGN KEY (source_plan_id)
  REFERENCES shrimp_bilibili_reliability_change_plans(id)
  ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_change_apply_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_reliability_change_plans(id) ON DELETE RESTRICT,
  decision text NOT NULL,
  reason text NOT NULL,
  actor text NOT NULL,
  plan_sha256 char(64) NOT NULL,
  dry_run_sha256 char(64) NOT NULL,
  decision_sha256 char(64) NOT NULL UNIQUE,
  decided_at timestamptz NOT NULL DEFAULT now(),
  CHECK (decision IN ('APPLY','REJECT')),
  CHECK (char_length(reason)>=3),
  CHECK (char_length(plan_sha256)=64),
  CHECK (char_length(dry_run_sha256)=64),
  CHECK (char_length(decision_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_policy_control_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid
    REFERENCES shrimp_bilibili_reliability_change_plans(id) ON DELETE RESTRICT,
  event_type text NOT NULL,
  previous_snapshot jsonb NOT NULL,
  next_snapshot jsonb NOT NULL,
  event_sha256 char(64) NOT NULL UNIQUE,
  actor text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (event_type IN ('PLAN_APPLIED')),
  CHECK (jsonb_typeof(previous_snapshot)='object'),
  CHECK (jsonb_typeof(next_snapshot)='object'),
  CHECK (char_length(event_sha256)=64)
);

CREATE OR REPLACE FUNCTION prevent_bilibili_reliability_change_plan_mutation()
RETURNS trigger AS $$
BEGIN
  IF OLD.plan_status='PENDING_APPLY'
     AND NEW.plan_status IN ('APPLIED','STALE')
     AND NEW.current_control_snapshot=OLD.current_control_snapshot
     AND NEW.proposed_control_snapshot=OLD.proposed_control_snapshot
     AND NEW.exact_change_set=OLD.exact_change_set
     AND NEW.dry_run_diff=OLD.dry_run_diff
     AND NEW.current_control_sha256=OLD.current_control_sha256
     AND NEW.proposed_control_sha256=OLD.proposed_control_sha256
     AND NEW.plan_sha256=OLD.plan_sha256
     AND NEW.dry_run_sha256=OLD.dry_run_sha256
     AND NEW.governance_evidence_sha256=OLD.governance_evidence_sha256
  THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Reliability change plan evidence is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_reliability_change_plan_mutation
  ON shrimp_bilibili_reliability_change_plans;
CREATE TRIGGER trg_prevent_bilibili_reliability_change_plan_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_reliability_change_plans
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_reliability_change_plan_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_reliability_apply_decision_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Reliability change apply decision is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_reliability_apply_decision_mutation
  ON shrimp_bilibili_reliability_change_apply_decisions;
CREATE TRIGGER trg_prevent_bilibili_reliability_apply_decision_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_reliability_change_apply_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_reliability_apply_decision_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_reliability_control_event_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Reliability policy control event is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_reliability_control_event_mutation
  ON shrimp_bilibili_reliability_policy_control_events;
CREATE TRIGGER trg_prevent_bilibili_reliability_control_event_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_reliability_policy_control_events
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_reliability_control_event_mutation();
