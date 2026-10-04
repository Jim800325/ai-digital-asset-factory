-- Step 10B.3 — Bilibili Account Manager v0.2
CREATE TABLE IF NOT EXISTS shrimp_bilibili_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_key text NOT NULL UNIQUE,
  display_name text NOT NULL,
  mid text NOT NULL UNIQUE,
  account_status text NOT NULL DEFAULT 'ACTIVE',
  tags jsonb NOT NULL DEFAULT '[]'::jsonb,
  default_tid integer NOT NULL DEFAULT 122,
  default_copyright text NOT NULL DEFAULT 'ORIGINAL',
  default_description text NOT NULL DEFAULT '',
  default_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
  cover_strategy text NOT NULL DEFAULT 'REQUIRE_ARTIFACT',
  daily_publish_limit integer NOT NULL DEFAULT 1,
  publish_window_start time,
  publish_window_end time,
  timezone text NOT NULL DEFAULT 'Asia/Shanghai',
  safety_policy jsonb NOT NULL DEFAULT '{"mode":"SACRIFICIAL","require_global_allowlist":true,"allow_public_visibility":false}'::jsonb,
  created_by text NOT NULL,
  updated_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (account_key ~ '^[A-Za-z0-9_.-]{3,120}$'),
  CHECK (char_length(display_name)>=1),
  CHECK (mid ~ '^[0-9]{1,32}$'),
  CHECK (account_status IN ('ACTIVE','INACTIVE')),
  CHECK (jsonb_typeof(tags)='array'),
  CHECK (default_tid>0),
  CHECK (default_copyright IN ('ORIGINAL','REPOST')),
  CHECK (jsonb_typeof(default_tags)='array'),
  CHECK (cover_strategy IN ('REQUIRE_ARTIFACT','OPTIONAL','NONE')),
  CHECK (daily_publish_limit BETWEEN 0 AND 100),
  CHECK (jsonb_typeof(safety_policy)='object')
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_accounts_status
  ON shrimp_bilibili_accounts(account_status,account_key);

ALTER TABLE shrimp_animation_publish_plans
  ADD COLUMN IF NOT EXISTS account_profile_id uuid
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS account_profile_sha256 char(64);

ALTER TABLE shrimp_animation_publish_plans
  DROP CONSTRAINT IF EXISTS ck_shrimp_publish_account_profile_hash;
ALTER TABLE shrimp_animation_publish_plans
  ADD CONSTRAINT ck_shrimp_publish_account_profile_hash
  CHECK (
    account_profile_sha256 IS NULL
    OR char_length(account_profile_sha256)=64
  );

CREATE OR REPLACE FUNCTION stale_shrimp_publish_plans_when_bilibili_account_changes()
RETURNS trigger AS $$
BEGIN
  IF NEW.display_name IS DISTINCT FROM OLD.display_name
     OR NEW.mid IS DISTINCT FROM OLD.mid
     OR NEW.account_status IS DISTINCT FROM OLD.account_status
     OR NEW.tags IS DISTINCT FROM OLD.tags
     OR NEW.default_tid IS DISTINCT FROM OLD.default_tid
     OR NEW.default_copyright IS DISTINCT FROM OLD.default_copyright
     OR NEW.default_description IS DISTINCT FROM OLD.default_description
     OR NEW.default_tags IS DISTINCT FROM OLD.default_tags
     OR NEW.cover_strategy IS DISTINCT FROM OLD.cover_strategy
     OR NEW.daily_publish_limit IS DISTINCT FROM OLD.daily_publish_limit
     OR NEW.publish_window_start IS DISTINCT FROM OLD.publish_window_start
     OR NEW.publish_window_end IS DISTINCT FROM OLD.publish_window_end
     OR NEW.timezone IS DISTINCT FROM OLD.timezone
     OR NEW.safety_policy IS DISTINCT FROM OLD.safety_policy
  THEN
    UPDATE shrimp_animation_publish_plans
    SET plan_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE account_profile_id=NEW.id
      AND plan_status<>'STALE';

    UPDATE shrimp_animation_publish_authorization_decisions
    SET decision_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE publish_plan_id IN (
      SELECT id FROM shrimp_animation_publish_plans
      WHERE account_profile_id=NEW.id
    )
      AND decision_status='CURRENT';
  END IF;
  NEW.updated_at=now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_bilibili_account_change
  ON shrimp_bilibili_accounts;
CREATE TRIGGER trg_shrimp_bilibili_account_change
BEFORE UPDATE ON shrimp_bilibili_accounts
FOR EACH ROW EXECUTE FUNCTION stale_shrimp_publish_plans_when_bilibili_account_changes();

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
