-- Step 10B.7 — Reservation Lifecycle + Execution Claim + Quota Ledger

DROP INDEX IF EXISTS uq_shrimp_bilibili_active_account_reservation;
CREATE UNIQUE INDEX uq_shrimp_bilibili_active_account_reservation
  ON shrimp_bilibili_publish_reservations(account_id)
  WHERE reservation_status IN ('HELD','CONSUMED');

DROP INDEX IF EXISTS uq_shrimp_bilibili_active_job_reservation;
CREATE UNIQUE INDEX uq_shrimp_bilibili_active_job_reservation
  ON shrimp_bilibili_publish_reservations(provider_job_id)
  WHERE reservation_status IN ('HELD','CONSUMED','CLAIMED','PUBLISHED');


ALTER TABLE shrimp_bilibili_publish_reservations
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_publish_reservations_reservation_status_check;
ALTER TABLE shrimp_bilibili_publish_reservations
  ADD CONSTRAINT shrimp_bilibili_publish_reservations_reservation_status_check
  CHECK (
    reservation_status IN (
      'HELD','CONSUMED','CLAIMED','PUBLISHED','SETTLED','RELEASED','EXPIRED'
    )
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_execution_claims (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reservation_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_publish_reservations(id) ON DELETE RESTRICT,
  execution_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_animation_publish_executions(id) ON DELETE CASCADE,
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  credential_slot_id uuid NOT NULL
    REFERENCES shrimp_bilibili_credential_slots(id) ON DELETE RESTRICT,
  target_id uuid NOT NULL
    REFERENCES shrimp_animation_publish_targets(id) ON DELETE RESTRICT,
  claim_status text NOT NULL DEFAULT 'CLAIMED',
  quota_units integer NOT NULL DEFAULT 1,
  claim_snapshot jsonb NOT NULL,
  claim_sha256 char(64) NOT NULL,
  claimed_by text NOT NULL,
  claimed_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  settled_at timestamptz,
  released_at timestamptz,
  release_reason text,
  CHECK (claim_status IN ('CLAIMED','PUBLISHED','SETTLED','RELEASED')),
  CHECK (quota_units=1),
  CHECK (char_length(claim_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_active_account_claim
  ON shrimp_bilibili_execution_claims(account_id)
  WHERE claim_status='CLAIMED';

CREATE TABLE IF NOT EXISTS shrimp_bilibili_quota_ledger (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  reservation_id uuid
    REFERENCES shrimp_bilibili_publish_reservations(id) ON DELETE RESTRICT,
  execution_claim_id uuid
    REFERENCES shrimp_bilibili_execution_claims(id) ON DELETE RESTRICT,
  execution_id uuid
    REFERENCES shrimp_animation_publish_executions(id) ON DELETE CASCADE,
  entry_type text NOT NULL,
  quota_units integer NOT NULL,
  local_quota_date date NOT NULL,
  account_timezone text NOT NULL,
  source_sha256 char(64) NOT NULL,
  entry_payload jsonb NOT NULL,
  entry_sha256 char(64) NOT NULL UNIQUE,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    entry_type IN (
      'CLAIM_CREATED',
      'PLAN_RELEASED',
      'RESERVATION_EXPIRED',
      'PUBLISH_COMMITTED',
      'CLEANUP_SETTLED',
      'CLAIM_RELEASED'
    )
  ),
  CHECK (quota_units IN (0,1)),
  CHECK (char_length(source_sha256)=64),
  CHECK (char_length(entry_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_quota_account_date
  ON shrimp_bilibili_quota_ledger(account_id,local_quota_date,entry_type);

ALTER TABLE shrimp_animation_publish_executions
  ADD COLUMN IF NOT EXISTS bilibili_execution_claim_id uuid
    REFERENCES shrimp_bilibili_execution_claims(id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION release_bilibili_reservation_on_plan_terminal()
RETURNS trigger AS $$
BEGIN
  IF NEW.plan_status IN ('PUBLISH_REJECTED','STALE')
     AND OLD.plan_status IS DISTINCT FROM NEW.plan_status
     AND NEW.reservation_id IS NOT NULL
  THEN
    UPDATE shrimp_bilibili_publish_reservations r
    SET reservation_status='RELEASED',
        released_at=COALESCE(r.released_at,now()),
        released_reason=COALESCE(
          r.released_reason,
          'PLAN_' || NEW.plan_status
        )
    WHERE r.id=NEW.reservation_id
      AND r.reservation_status='CONSUMED'
      AND NOT EXISTS (
        SELECT 1
        FROM shrimp_bilibili_execution_claims c
        WHERE c.reservation_id=r.id
      );
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_release_bilibili_reservation_on_plan_terminal
  ON shrimp_animation_publish_plans;
CREATE TRIGGER trg_release_bilibili_reservation_on_plan_terminal
AFTER UPDATE OF plan_status ON shrimp_animation_publish_plans
FOR EACH ROW
EXECUTE FUNCTION release_bilibili_reservation_on_plan_terminal();

CREATE OR REPLACE FUNCTION prevent_bilibili_quota_ledger_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili quota ledger is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_quota_ledger_update
  ON shrimp_bilibili_quota_ledger;
CREATE TRIGGER trg_prevent_bilibili_quota_ledger_update
BEFORE UPDATE OR DELETE ON shrimp_bilibili_quota_ledger
FOR EACH ROW
EXECUTE FUNCTION prevent_bilibili_quota_ledger_mutation();


CREATE OR REPLACE FUNCTION record_bilibili_reservation_release_ledger()
RETURNS trigger AS $$
DECLARE tz text;
DECLARE local_day date;
DECLARE payload jsonb;
DECLARE entry_hash text;
BEGIN
  IF OLD.reservation_status='CONSUMED'
     AND NEW.reservation_status='RELEASED'
  THEN
    SELECT timezone INTO tz
    FROM shrimp_bilibili_accounts
    WHERE id=NEW.account_id;

    local_day=(now() AT TIME ZONE COALESCE(tz,'Asia/Shanghai'))::date;
    payload=jsonb_build_object(
      'schema_version','shrimp-bilibili-quota-ledger-v0.1',
      'entry_type','PLAN_RELEASED',
      'reservation_id',NEW.id::text,
      'account_id',NEW.account_id::text,
      'reason',COALESCE(NEW.released_reason,'PLAN_TERMINAL'),
      'selection_sha256',NEW.selection_sha256
    );
    entry_hash=encode(digest(payload::text,'sha256'),'hex');

    INSERT INTO shrimp_bilibili_quota_ledger(
      account_id,reservation_id,entry_type,quota_units,
      local_quota_date,account_timezone,source_sha256,
      entry_payload,entry_sha256,created_by)
    VALUES(
      NEW.account_id,NEW.id,'PLAN_RELEASED',0,
      local_day,COALESCE(tz,'Asia/Shanghai'),NEW.selection_sha256,
      payload,entry_hash,'reservation-lifecycle-trigger')
    ON CONFLICT (entry_sha256) DO NOTHING;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_record_bilibili_reservation_release_ledger
  ON shrimp_bilibili_publish_reservations;
CREATE TRIGGER trg_record_bilibili_reservation_release_ledger
AFTER UPDATE OF reservation_status ON shrimp_bilibili_publish_reservations
FOR EACH ROW
EXECUTE FUNCTION record_bilibili_reservation_release_ledger();


CREATE OR REPLACE FUNCTION record_bilibili_reservation_expiry_ledger()
RETURNS trigger AS $$
DECLARE tz text;
DECLARE local_day date;
DECLARE payload jsonb;
DECLARE entry_hash text;
BEGIN
  IF OLD.reservation_status='HELD'
     AND NEW.reservation_status='EXPIRED'
  THEN
    SELECT timezone INTO tz
    FROM shrimp_bilibili_accounts
    WHERE id=NEW.account_id;

    local_day=(now() AT TIME ZONE COALESCE(tz,'Asia/Shanghai'))::date;
    payload=jsonb_build_object(
      'schema_version','shrimp-bilibili-quota-ledger-v0.1',
      'entry_type','RESERVATION_EXPIRED',
      'reservation_id',NEW.id::text,
      'account_id',NEW.account_id::text,
      'reason',COALESCE(NEW.released_reason,'TTL_EXPIRED'),
      'selection_sha256',NEW.selection_sha256
    );
    entry_hash=encode(digest(payload::text,'sha256'),'hex');

    INSERT INTO shrimp_bilibili_quota_ledger(
      account_id,reservation_id,entry_type,quota_units,
      local_quota_date,account_timezone,source_sha256,
      entry_payload,entry_sha256,created_by)
    VALUES(
      NEW.account_id,NEW.id,'RESERVATION_EXPIRED',0,
      local_day,COALESCE(tz,'Asia/Shanghai'),NEW.selection_sha256,
      payload,entry_hash,'reservation-expiry-trigger')
    ON CONFLICT (entry_sha256) DO NOTHING;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_record_bilibili_reservation_expiry_ledger
  ON shrimp_bilibili_publish_reservations;
CREATE TRIGGER trg_record_bilibili_reservation_expiry_ledger
AFTER UPDATE OF reservation_status ON shrimp_bilibili_publish_reservations
FOR EACH ROW
EXECUTE FUNCTION record_bilibili_reservation_expiry_ledger();
