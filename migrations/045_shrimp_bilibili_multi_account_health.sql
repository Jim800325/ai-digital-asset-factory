-- Step 10B.4 — Multi-account Credential Binding + Health Check
CREATE TABLE IF NOT EXISTS shrimp_bilibili_credential_slots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slot_key text NOT NULL UNIQUE,
  account_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE CASCADE,
  env_prefix text NOT NULL UNIQUE,
  slot_status text NOT NULL DEFAULT 'ACTIVE',
  credential_status text NOT NULL DEFAULT 'UNKNOWN',
  login_status text NOT NULL DEFAULT 'UNKNOWN',
  mid_status text NOT NULL DEFAULT 'UNKNOWN',
  publish_permission_status text NOT NULL DEFAULT 'UNKNOWN',
  health_status text NOT NULL DEFAULT 'UNKNOWN',
  provider_mid text,
  provider_uname text,
  provider_level integer,
  last_checked_at timestamptz,
  last_success_at timestamptz,
  last_failure_at timestamptz,
  last_error_type text,
  last_error_sha256 char(64),
  health_evidence_sha256 char(64),
  created_by text NOT NULL,
  updated_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (slot_key ~ '^[A-Za-z0-9_.-]{3,120}$'),
  CHECK (env_prefix ~ '^[A-Z][A-Z0-9_]{2,120}$'),
  CHECK (slot_status IN ('ACTIVE','INACTIVE')),
  CHECK (credential_status IN ('UNKNOWN','CONFIGURED','MISSING')),
  CHECK (login_status IN ('UNKNOWN','LOGGED_IN','LOGGED_OUT','ERROR')),
  CHECK (mid_status IN ('UNKNOWN','MATCH','MISMATCH','ERROR')),
  CHECK (publish_permission_status IN ('UNKNOWN','ALLOWED','DENIED','ERROR')),
  CHECK (health_status IN ('UNKNOWN','HEALTHY','DEGRADED','UNHEALTHY')),
  CHECK (last_error_sha256 IS NULL OR char_length(last_error_sha256)=64),
  CHECK (health_evidence_sha256 IS NULL OR char_length(health_evidence_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_credential_health
  ON shrimp_bilibili_credential_slots(health_status,slot_status,last_checked_at DESC);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_health_checks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slot_id uuid NOT NULL
    REFERENCES shrimp_bilibili_credential_slots(id) ON DELETE CASCADE,
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE CASCADE,
  expected_mid text NOT NULL,
  provider_mid text,
  login_ok boolean NOT NULL DEFAULT false,
  mid_match boolean NOT NULL DEFAULT false,
  publish_probe_ok boolean NOT NULL DEFAULT false,
  credential_present boolean NOT NULL DEFAULT false,
  health_status text NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  failure_type text,
  checked_by text NOT NULL,
  checked_at timestamptz NOT NULL DEFAULT now(),
  CHECK (health_status IN ('HEALTHY','DEGRADED','UNHEALTHY')),
  CHECK (char_length(evidence_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_health_checks_slot
  ON shrimp_bilibili_health_checks(slot_id,checked_at DESC);

ALTER TABLE shrimp_animation_publish_plans
  ADD COLUMN IF NOT EXISTS credential_slot_id uuid
    REFERENCES shrimp_bilibili_credential_slots(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS credential_slot_sha256 char(64);

ALTER TABLE shrimp_animation_publish_plans
  DROP CONSTRAINT IF EXISTS ck_shrimp_publish_credential_slot_hash;
ALTER TABLE shrimp_animation_publish_plans
  ADD CONSTRAINT ck_shrimp_publish_credential_slot_hash
  CHECK (
    credential_slot_sha256 IS NULL
    OR char_length(credential_slot_sha256)=64
  );

CREATE OR REPLACE FUNCTION stale_shrimp_publish_plans_when_credential_slot_changes()
RETURNS trigger AS $$
BEGIN
  IF NEW.account_id IS DISTINCT FROM OLD.account_id
     OR NEW.env_prefix IS DISTINCT FROM OLD.env_prefix
     OR NEW.slot_status IS DISTINCT FROM OLD.slot_status
     OR NEW.health_status IS DISTINCT FROM OLD.health_status
     OR NEW.mid_status IS DISTINCT FROM OLD.mid_status
     OR NEW.publish_permission_status IS DISTINCT FROM OLD.publish_permission_status
  THEN
    UPDATE shrimp_animation_publish_plans
    SET plan_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE credential_slot_id=NEW.id
      AND plan_status<>'STALE';

    UPDATE shrimp_animation_publish_authorization_decisions
    SET decision_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE publish_plan_id IN (
      SELECT id FROM shrimp_animation_publish_plans
      WHERE credential_slot_id=NEW.id
    )
      AND decision_status='CURRENT';
  END IF;
  NEW.updated_at=now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_bilibili_credential_slot_change
  ON shrimp_bilibili_credential_slots;
CREATE TRIGGER trg_shrimp_bilibili_credential_slot_change
BEFORE UPDATE ON shrimp_bilibili_credential_slots
FOR EACH ROW EXECUTE FUNCTION stale_shrimp_publish_plans_when_credential_slot_changes();

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
