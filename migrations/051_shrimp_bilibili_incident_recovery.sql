-- Step 10B.10 — Publisher Incident Timeline + Recovery Approval Gate + Notifications

CREATE TABLE IF NOT EXISTS shrimp_bilibili_incidents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_key text NOT NULL UNIQUE,
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  execution_claim_id uuid
    REFERENCES shrimp_bilibili_execution_claims(id) ON DELETE SET NULL,
  escalation_id uuid
    REFERENCES shrimp_bilibili_claim_escalations(id) ON DELETE SET NULL,
  circuit_breaker_id uuid
    REFERENCES shrimp_bilibili_account_circuit_breakers(id) ON DELETE SET NULL,
  severity text NOT NULL,
  incident_status text NOT NULL DEFAULT 'OPEN',
  title text NOT NULL,
  summary text NOT NULL,
  evidence_snapshot jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  opened_by text NOT NULL,
  opened_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  resolution_summary text,
  CHECK (severity IN ('WARNING','CRITICAL')),
  CHECK (incident_status IN ('OPEN','RECOVERY_REVIEW','RESOLVED')),
  CHECK (char_length(evidence_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_open_incident_per_account
  ON shrimp_bilibili_incidents(account_id)
  WHERE incident_status IN ('OPEN','RECOVERY_REVIEW');

CREATE TABLE IF NOT EXISTS shrimp_bilibili_incident_timeline (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id uuid NOT NULL
    REFERENCES shrimp_bilibili_incidents(id) ON DELETE CASCADE,
  event_type text NOT NULL,
  source_type text NOT NULL,
  source_id text,
  event_payload jsonb NOT NULL,
  event_sha256 char(64) NOT NULL UNIQUE,
  actor text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    event_type IN (
      'INCIDENT_OPENED','ESCALATION_LINKED','READBACK_EVIDENCE',
      'CIRCUIT_EVENT','HEALTH_EVIDENCE','RECOVERY_REQUESTED',
      'RECOVERY_APPROVED','RECOVERY_REJECTED',
      'RECOVERY_APPLIED','INCIDENT_RESOLVED','NOTIFICATION_QUEUED',
      'NOTIFICATION_DELIVERED','NOTIFICATION_FAILED'
    )
  )
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_incident_timeline
  ON shrimp_bilibili_incident_timeline(incident_id,created_at,id);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_recovery_approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id uuid NOT NULL
    REFERENCES shrimp_bilibili_incidents(id) ON DELETE CASCADE,
  account_id uuid NOT NULL
    REFERENCES shrimp_bilibili_accounts(id) ON DELETE RESTRICT,
  circuit_breaker_id uuid NOT NULL
    REFERENCES shrimp_bilibili_account_circuit_breakers(id) ON DELETE RESTRICT,
  request_status text NOT NULL DEFAULT 'PENDING',
  evidence_snapshot jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  requested_by text NOT NULL,
  requested_at timestamptz NOT NULL DEFAULT now(),
  decision text,
  decision_reason text,
  decision_by text,
  decided_at timestamptz,
  CHECK (request_status IN ('PENDING','APPROVED','REJECTED','APPLIED','STALE')),
  CHECK (decision IS NULL OR decision IN ('APPROVE','REJECT')),
  CHECK (char_length(evidence_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_pending_recovery_approval
  ON shrimp_bilibili_recovery_approvals(incident_id)
  WHERE request_status='PENDING';

CREATE TABLE IF NOT EXISTS shrimp_bilibili_notification_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id uuid
    REFERENCES shrimp_bilibili_incidents(id) ON DELETE CASCADE,
  notification_type text NOT NULL,
  severity text NOT NULL,
  destination_type text NOT NULL DEFAULT 'CONTROL_CENTER',
  destination_ref text,
  payload jsonb NOT NULL,
  payload_sha256 char(64) NOT NULL UNIQUE,
  delivery_status text NOT NULL DEFAULT 'QUEUED',
  attempt_count integer NOT NULL DEFAULT 0,
  last_attempt_at timestamptz,
  delivered_at timestamptz,
  last_error_type text,
  last_error_sha256 char(64),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (notification_type IN ('INCIDENT_OPENED','RECOVERY_REQUIRED','RECOVERY_APPROVED','INCIDENT_RESOLVED')),
  CHECK (severity IN ('INFO','WARNING','CRITICAL')),
  CHECK (destination_type IN ('CONTROL_CENTER','WEBHOOK')),
  CHECK (delivery_status IN ('QUEUED','DELIVERED','FAILED')),
  CHECK (attempt_count>=0),
  CHECK (char_length(payload_sha256)=64),
  CHECK (last_error_sha256 IS NULL OR char_length(last_error_sha256)=64)
);

CREATE OR REPLACE FUNCTION prevent_bilibili_incident_timeline_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili incident timeline is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_incident_timeline_mutation
  ON shrimp_bilibili_incident_timeline;
CREATE TRIGGER trg_prevent_bilibili_incident_timeline_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_incident_timeline
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_incident_timeline_mutation();
