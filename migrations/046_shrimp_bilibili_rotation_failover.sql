-- Step 10B.5 — Credential Rotation + Scheduled Health Monitor + Failover
ALTER TABLE shrimp_bilibili_credential_slots
  ADD COLUMN IF NOT EXISTS credential_version integer NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS previous_env_prefix text,
  ADD COLUMN IF NOT EXISTS rotated_at timestamptz,
  ADD COLUMN IF NOT EXISTS rotation_evidence_sha256 char(64),
  ADD COLUMN IF NOT EXISTS consecutive_failures integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS degradation_status text NOT NULL DEFAULT 'NORMAL',
  ADD COLUMN IF NOT EXISTS selection_priority integer NOT NULL DEFAULT 100,
  ADD COLUMN IF NOT EXISTS last_monitor_run_id uuid;

ALTER TABLE shrimp_bilibili_credential_slots
  DROP CONSTRAINT IF EXISTS ck_shrimp_bilibili_credential_version;
ALTER TABLE shrimp_bilibili_credential_slots
  ADD CONSTRAINT ck_shrimp_bilibili_credential_version
  CHECK (credential_version>=1);

ALTER TABLE shrimp_bilibili_credential_slots
  DROP CONSTRAINT IF EXISTS ck_shrimp_bilibili_consecutive_failures;
ALTER TABLE shrimp_bilibili_credential_slots
  ADD CONSTRAINT ck_shrimp_bilibili_consecutive_failures
  CHECK (consecutive_failures>=0);

ALTER TABLE shrimp_bilibili_credential_slots
  DROP CONSTRAINT IF EXISTS ck_shrimp_bilibili_degradation_status;
ALTER TABLE shrimp_bilibili_credential_slots
  ADD CONSTRAINT ck_shrimp_bilibili_degradation_status
  CHECK (degradation_status IN ('NORMAL','DEGRADED','QUARANTINED'));

ALTER TABLE shrimp_bilibili_credential_slots
  DROP CONSTRAINT IF EXISTS ck_shrimp_bilibili_selection_priority;
ALTER TABLE shrimp_bilibili_credential_slots
  ADD CONSTRAINT ck_shrimp_bilibili_selection_priority
  CHECK (selection_priority BETWEEN 1 AND 10000);

ALTER TABLE shrimp_bilibili_credential_slots
  DROP CONSTRAINT IF EXISTS ck_shrimp_bilibili_rotation_sha;
ALTER TABLE shrimp_bilibili_credential_slots
  ADD CONSTRAINT ck_shrimp_bilibili_rotation_sha
  CHECK (
    rotation_evidence_sha256 IS NULL
    OR char_length(rotation_evidence_sha256)=64
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_credential_rotations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slot_id uuid NOT NULL
    REFERENCES shrimp_bilibili_credential_slots(id) ON DELETE CASCADE,
  previous_version integer NOT NULL,
  new_version integer NOT NULL,
  previous_env_prefix text NOT NULL,
  new_env_prefix text NOT NULL,
  reason text NOT NULL,
  rotation_evidence_sha256 char(64) NOT NULL,
  rotated_by text NOT NULL,
  rotated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (new_version=previous_version+1),
  CHECK (char_length(reason)>=3),
  CHECK (char_length(rotation_evidence_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_rotations_slot
  ON shrimp_bilibili_credential_rotations(slot_id,rotated_at DESC);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_health_monitor_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  monitor_status text NOT NULL DEFAULT 'RUNNING',
  active_slot_count integer NOT NULL DEFAULT 0,
  checked_slot_count integer NOT NULL DEFAULT 0,
  healthy_slot_count integer NOT NULL DEFAULT 0,
  degraded_slot_count integer NOT NULL DEFAULT 0,
  unhealthy_slot_count integer NOT NULL DEFAULT 0,
  quarantined_slot_count integer NOT NULL DEFAULT 0,
  selected_account_key text,
  selected_slot_key text,
  started_by text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  summary_sha256 char(64),
  CHECK (monitor_status IN ('RUNNING','SUCCEEDED','PARTIAL','FAILED')),
  CHECK (summary_sha256 IS NULL OR char_length(summary_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_health_monitor_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  monitor_run_id uuid NOT NULL
    REFERENCES shrimp_bilibili_health_monitor_runs(id) ON DELETE CASCADE,
  slot_id uuid NOT NULL
    REFERENCES shrimp_bilibili_credential_slots(id) ON DELETE CASCADE,
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE CASCADE,
  slot_key text NOT NULL,
  credential_version integer NOT NULL,
  health_status text NOT NULL,
  degradation_status text NOT NULL,
  consecutive_failures integer NOT NULL,
  health_check_id uuid
    REFERENCES shrimp_bilibili_health_checks(id) ON DELETE SET NULL,
  failure_type text,
  item_sha256 char(64) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (char_length(item_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_monitor_items_run
  ON shrimp_bilibili_health_monitor_items(monitor_run_id,created_at,id);

ALTER TABLE shrimp_bilibili_credential_slots
  ADD CONSTRAINT fk_shrimp_bilibili_last_monitor_run
  FOREIGN KEY (last_monitor_run_id)
  REFERENCES shrimp_bilibili_health_monitor_runs(id)
  ON DELETE SET NULL
  NOT VALID;

ALTER TABLE shrimp_animation_publish_plans
  ADD COLUMN IF NOT EXISTS credential_version integer,
  ADD COLUMN IF NOT EXISTS failover_selection_sha256 char(64);

ALTER TABLE shrimp_animation_publish_plans
  DROP CONSTRAINT IF EXISTS ck_shrimp_publish_credential_version;
ALTER TABLE shrimp_animation_publish_plans
  ADD CONSTRAINT ck_shrimp_publish_credential_version
  CHECK (credential_version IS NULL OR credential_version>=1);

ALTER TABLE shrimp_animation_publish_plans
  DROP CONSTRAINT IF EXISTS ck_shrimp_publish_failover_sha;
ALTER TABLE shrimp_animation_publish_plans
  ADD CONSTRAINT ck_shrimp_publish_failover_sha
  CHECK (
    failover_selection_sha256 IS NULL
    OR char_length(failover_selection_sha256)=64
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
     OR NEW.account_profile_id IS DISTINCT FROM OLD.account_profile_id
     OR NEW.account_profile_sha256 IS DISTINCT FROM OLD.account_profile_sha256
     OR NEW.credential_slot_id IS DISTINCT FROM OLD.credential_slot_id
     OR NEW.credential_slot_sha256 IS DISTINCT FROM OLD.credential_slot_sha256
     OR NEW.credential_version IS DISTINCT FROM OLD.credential_version
     OR NEW.failover_selection_sha256 IS DISTINCT FROM OLD.failover_selection_sha256
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
