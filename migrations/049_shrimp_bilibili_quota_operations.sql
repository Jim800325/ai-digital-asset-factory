-- Step 10B.8 — Quota Operations + Stuck Claim Reconciliation + Daily Reset Audit

CREATE TABLE IF NOT EXISTS shrimp_bilibili_stuck_claim_reconciliations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_claim_id uuid NOT NULL
    REFERENCES shrimp_bilibili_execution_claims(id) ON DELETE CASCADE,
  execution_id uuid NOT NULL
    REFERENCES shrimp_animation_publish_executions(id) ON DELETE CASCADE,
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  detected_status text NOT NULL,
  execution_status_before text NOT NULL,
  execution_status_after text NOT NULL,
  reconciliation_action text NOT NULL,
  reconciliation_outcome text NOT NULL,
  provider_write_performed boolean NOT NULL DEFAULT false,
  claim_status_after text NOT NULL,
  evidence_payload jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL UNIQUE,
  reconciled_by text NOT NULL,
  reconciled_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    reconciliation_action IN (
      'NONE_REQUIRED',
      'UPLOAD_READBACK',
      'PUBLISH_READBACK',
      'WAIT_FOR_PROVIDER',
      'MANUAL_REVIEW_REQUIRED'
    )
  ),
  CHECK (
    reconciliation_outcome IN (
      'NO_ACTION',
      'RECONCILED',
      'STILL_AMBIGUOUS',
      'BLOCKED',
      'FAILED'
    )
  ),
  CHECK (char_length(evidence_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_stuck_claim_reconciliation
  ON shrimp_bilibili_stuck_claim_reconciliations(
    execution_claim_id,reconciled_at DESC
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_daily_quota_audits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  local_quota_date date NOT NULL,
  account_timezone text NOT NULL,
  daily_publish_limit integer NOT NULL,
  published_units integer NOT NULL,
  held_units integer NOT NULL,
  claimed_units integer NOT NULL,
  available_units integer NOT NULL,
  carryover_claim_count integer NOT NULL,
  carryover_reservation_count integer NOT NULL,
  previous_day_published_units integer NOT NULL,
  audit_status text NOT NULL,
  audit_payload jsonb NOT NULL,
  audit_sha256 char(64) NOT NULL UNIQUE,
  audited_by text NOT NULL,
  audited_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(account_id,local_quota_date),
  CHECK (
    audit_status IN ('BALANCED','CARRYOVER_PRESENT','OVER_LIMIT','INCONSISTENT')
  ),
  CHECK (published_units>=0),
  CHECK (held_units>=0),
  CHECK (claimed_units>=0),
  CHECK (available_units>=0),
  CHECK (carryover_claim_count>=0),
  CHECK (carryover_reservation_count>=0),
  CHECK (previous_day_published_units>=0),
  CHECK (char_length(audit_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_daily_quota_audit_date
  ON shrimp_bilibili_daily_quota_audits(local_quota_date,audit_status);

CREATE OR REPLACE FUNCTION prevent_bilibili_daily_quota_audit_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili daily quota audit is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_daily_quota_audit_mutation
  ON shrimp_bilibili_daily_quota_audits;
CREATE TRIGGER trg_prevent_bilibili_daily_quota_audit_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_daily_quota_audits
FOR EACH ROW
EXECUTE FUNCTION prevent_bilibili_daily_quota_audit_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_stuck_reconciliation_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili stuck reconciliation audit is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_stuck_reconciliation_mutation
  ON shrimp_bilibili_stuck_claim_reconciliations;
CREATE TRIGGER trg_prevent_bilibili_stuck_reconciliation_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_stuck_claim_reconciliations
FOR EACH ROW
EXECUTE FUNCTION prevent_bilibili_stuck_reconciliation_mutation();
