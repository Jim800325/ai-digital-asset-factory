-- Step 10B.6 — Health-Aware Router + Pre-Publish Reservation
CREATE TABLE IF NOT EXISTS shrimp_bilibili_publish_reservations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_job_id uuid NOT NULL
    REFERENCES shrimp_animation_jobs(provider_job_id) ON DELETE CASCADE,
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  credential_slot_id uuid NOT NULL
    REFERENCES shrimp_bilibili_credential_slots(id) ON DELETE RESTRICT,
  target_id uuid NOT NULL
    REFERENCES shrimp_animation_publish_targets(id) ON DELETE RESTRICT,
  reservation_status text NOT NULL DEFAULT 'HELD',
  reservation_key text NOT NULL UNIQUE,
  reserved_publish_units integer NOT NULL DEFAULT 1,
  selection_rank integer NOT NULL,
  account_key text NOT NULL,
  slot_key text NOT NULL,
  target_key text NOT NULL,
  selection_snapshot jsonb NOT NULL,
  selection_sha256 char(64) NOT NULL,
  expires_at timestamptz NOT NULL,
  consumed_by_plan_id uuid
    REFERENCES shrimp_animation_publish_plans(id) ON DELETE SET NULL,
  released_reason text,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  consumed_at timestamptz,
  released_at timestamptz,
  CHECK (reservation_status IN ('HELD','CONSUMED','RELEASED','EXPIRED')),
  CHECK (reserved_publish_units=1),
  CHECK (selection_rank>=1),
  CHECK (char_length(selection_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_active_account_reservation
  ON shrimp_bilibili_publish_reservations(account_id)
  WHERE reservation_status='HELD';

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_active_job_reservation
  ON shrimp_bilibili_publish_reservations(provider_job_id)
  WHERE reservation_status='HELD';

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_reservation_expiry
  ON shrimp_bilibili_publish_reservations(reservation_status,expires_at);

ALTER TABLE shrimp_animation_publish_plans
  ADD COLUMN IF NOT EXISTS reservation_id uuid
    REFERENCES shrimp_bilibili_publish_reservations(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS reservation_sha256 char(64);

ALTER TABLE shrimp_animation_publish_plans
  DROP CONSTRAINT IF EXISTS ck_shrimp_publish_reservation_sha;
ALTER TABLE shrimp_animation_publish_plans
  ADD CONSTRAINT ck_shrimp_publish_reservation_sha
  CHECK (
    reservation_sha256 IS NULL
    OR char_length(reservation_sha256)=64
  );

CREATE OR REPLACE FUNCTION expire_shrimp_bilibili_reservations()
RETURNS integer AS $$
DECLARE affected integer;
BEGIN
  UPDATE shrimp_bilibili_publish_reservations
  SET reservation_status='EXPIRED',
      released_at=COALESCE(released_at,now()),
      released_reason=COALESCE(released_reason,'TTL_EXPIRED')
  WHERE reservation_status='HELD'
    AND expires_at<=now();
  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN affected;
END;
$$ LANGUAGE plpgsql;

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
     OR NEW.reservation_id IS DISTINCT FROM OLD.reservation_id
     OR NEW.reservation_sha256 IS DISTINCT FROM OLD.reservation_sha256
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
