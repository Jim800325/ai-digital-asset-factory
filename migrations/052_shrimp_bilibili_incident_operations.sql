-- Step 10B.11 — Incident Acknowledgement + On-Call Routing + Recovery SLA + PIR

ALTER TABLE shrimp_bilibili_incidents
  ADD COLUMN IF NOT EXISTS acknowledgement_status text NOT NULL DEFAULT 'UNACKNOWLEDGED',
  ADD COLUMN IF NOT EXISTS acknowledged_by text,
  ADD COLUMN IF NOT EXISTS acknowledged_at timestamptz,
  ADD COLUMN IF NOT EXISTS owner_ref text,
  ADD COLUMN IF NOT EXISTS owner_assigned_at timestamptz,
  ADD COLUMN IF NOT EXISTS ack_due_at timestamptz,
  ADD COLUMN IF NOT EXISTS recovery_due_at timestamptz,
  ADD COLUMN IF NOT EXISTS sla_status text NOT NULL DEFAULT 'WITHIN_SLA',
  ADD COLUMN IF NOT EXISTS pir_status text NOT NULL DEFAULT 'NOT_REQUIRED';

ALTER TABLE shrimp_bilibili_incidents
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_incidents_ack_status_check;
ALTER TABLE shrimp_bilibili_incidents
  ADD CONSTRAINT shrimp_bilibili_incidents_ack_status_check
  CHECK (acknowledgement_status IN ('UNACKNOWLEDGED','ACKNOWLEDGED'));

ALTER TABLE shrimp_bilibili_incidents
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_incidents_sla_status_check;
ALTER TABLE shrimp_bilibili_incidents
  ADD CONSTRAINT shrimp_bilibili_incidents_sla_status_check
  CHECK (sla_status IN ('WITHIN_SLA','ACK_BREACHED','RECOVERY_BREACHED','RECOVERED'));

ALTER TABLE shrimp_bilibili_incidents
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_incidents_pir_status_check;
ALTER TABLE shrimp_bilibili_incidents
  ADD CONSTRAINT shrimp_bilibili_incidents_pir_status_check
  CHECK (pir_status IN ('NOT_REQUIRED','REQUIRED','IN_PROGRESS','COMPLETED'));

CREATE TABLE IF NOT EXISTS shrimp_bilibili_oncall_routes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  route_key text NOT NULL UNIQUE,
  severity text NOT NULL,
  owner_ref text NOT NULL,
  secondary_owner_ref text,
  route_status text NOT NULL DEFAULT 'ACTIVE',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (severity IN ('WARNING','CRITICAL')),
  CHECK (route_status IN ('ACTIVE','DISABLED'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_active_oncall_severity
  ON shrimp_bilibili_oncall_routes(severity)
  WHERE route_status='ACTIVE';

CREATE TABLE IF NOT EXISTS shrimp_bilibili_incident_sla_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id uuid NOT NULL
    REFERENCES shrimp_bilibili_incidents(id) ON DELETE CASCADE,
  event_type text NOT NULL,
  previous_status text NOT NULL,
  next_status text NOT NULL,
  due_at timestamptz,
  observed_at timestamptz NOT NULL DEFAULT now(),
  event_payload jsonb NOT NULL,
  event_sha256 char(64) NOT NULL UNIQUE,
  actor text NOT NULL,
  CHECK (
    event_type IN (
      'ACK_DUE_SET','ACK_BREACHED',
      'RECOVERY_DUE_SET','RECOVERY_BREACHED',
      'SLA_RECOVERED'
    )
  ),
  CHECK (char_length(event_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_incident_sla_events
  ON shrimp_bilibili_incident_sla_events(incident_id,observed_at);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_post_incident_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_incidents(id) ON DELETE CASCADE,
  review_status text NOT NULL DEFAULT 'DRAFT',
  root_cause text,
  contributing_factors jsonb NOT NULL DEFAULT '[]'::jsonb,
  customer_impact text,
  detection_gap text,
  recovery_notes text,
  lessons_learned text,
  review_sha256 char(64),
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_by text,
  completed_at timestamptz,
  CHECK (review_status IN ('DRAFT','IN_REVIEW','COMPLETED')),
  CHECK (review_sha256 IS NULL OR char_length(review_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_corrective_actions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pir_id uuid NOT NULL
    REFERENCES shrimp_bilibili_post_incident_reviews(id) ON DELETE CASCADE,
  incident_id uuid NOT NULL
    REFERENCES shrimp_bilibili_incidents(id) ON DELETE CASCADE,
  action_key text NOT NULL,
  description text NOT NULL,
  owner_ref text NOT NULL,
  due_at timestamptz,
  action_status text NOT NULL DEFAULT 'OPEN',
  completion_evidence text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE(pir_id,action_key),
  CHECK (action_status IN ('OPEN','IN_PROGRESS','COMPLETED','CANCELLED'))
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_corrective_actions_due
  ON shrimp_bilibili_corrective_actions(action_status,due_at);

ALTER TABLE shrimp_bilibili_incident_timeline
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_incident_timeline_event_type_check;
ALTER TABLE shrimp_bilibili_incident_timeline
  ADD CONSTRAINT shrimp_bilibili_incident_timeline_event_type_check
  CHECK (
    event_type IN (
      'INCIDENT_OPENED','ESCALATION_LINKED','READBACK_EVIDENCE',
      'CIRCUIT_EVENT','HEALTH_EVIDENCE','RECOVERY_REQUESTED',
      'RECOVERY_APPROVED','RECOVERY_REJECTED',
      'RECOVERY_APPLIED','INCIDENT_RESOLVED','NOTIFICATION_QUEUED',
      'NOTIFICATION_DELIVERED','NOTIFICATION_FAILED',
      'INCIDENT_ACKNOWLEDGED','OWNER_ASSIGNED',
      'ACK_SLA_BREACHED','RECOVERY_SLA_BREACHED',
      'PIR_STARTED','PIR_COMPLETED','CORRECTIVE_ACTION_ADDED',
      'CORRECTIVE_ACTION_COMPLETED'
    )
  );

CREATE OR REPLACE FUNCTION prevent_bilibili_incident_sla_event_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili incident SLA event log is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_incident_sla_event_mutation
  ON shrimp_bilibili_incident_sla_events;
CREATE TRIGGER trg_prevent_bilibili_incident_sla_event_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_incident_sla_events
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_incident_sla_event_mutation();
