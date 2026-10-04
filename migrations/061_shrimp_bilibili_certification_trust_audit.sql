-- Step 10B.20 — Certification Trust Chain Verification + Integrity Audit + Renewal SLA + Missed-Renewal Escalation

CREATE TABLE IF NOT EXISTS shrimp_bilibili_certification_integrity_audits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  audit_status text NOT NULL,
  certification_count integer NOT NULL,
  attestation_count integer NOT NULL,
  current_certification_count integer NOT NULL,
  issue_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
  audit_snapshot jsonb NOT NULL,
  audit_snapshot_sha256 char(64) NOT NULL,
  audit_sha256 char(64) NOT NULL UNIQUE,
  evaluated_by text NOT NULL,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (audit_status IN ('PASS','FAIL')),
  CHECK (certification_count>=0),
  CHECK (attestation_count>=0),
  CHECK (current_certification_count>=0),
  CHECK (jsonb_typeof(issue_codes)='array'),
  CHECK (jsonb_typeof(audit_snapshot)='object'),
  CHECK (char_length(audit_snapshot_sha256)=64),
  CHECK (char_length(audit_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_cert_integrity_audit
  ON shrimp_bilibili_certification_integrity_audits(
    evaluated_at DESC,audit_status
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_renewal_sla_escalations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  certification_id uuid NOT NULL
    REFERENCES shrimp_bilibili_post_restore_certifications(id) ON DELETE RESTRICT,
  escalation_type text NOT NULL,
  severity text NOT NULL,
  escalation_status text NOT NULL DEFAULT 'OPEN',
  due_at timestamptz NOT NULL,
  breached_at timestamptz NOT NULL,
  overdue_minutes integer NOT NULL,
  evidence_snapshot jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  escalation_sha256 char(64) NOT NULL UNIQUE,
  opened_by text NOT NULL,
  opened_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz,
  CHECK (
    escalation_type IN (
      'RENEWAL_SLA_BREACH',
      'MISSED_RENEWAL',
      'MISSED_RENEWAL_CRITICAL'
    )
  ),
  CHECK (severity IN ('WARNING','CRITICAL')),
  CHECK (escalation_status IN ('OPEN','CLOSED','SUPERSEDED')),
  CHECK (overdue_minutes>=0),
  CHECK (jsonb_typeof(evidence_snapshot)='object'),
  CHECK (char_length(evidence_sha256)=64),
  CHECK (char_length(escalation_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_open_renewal_escalation
  ON shrimp_bilibili_renewal_sla_escalations(certification_id,escalation_type)
  WHERE escalation_status='OPEN';

CREATE TABLE IF NOT EXISTS shrimp_bilibili_certification_audit_proofs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  integrity_audit_id uuid NOT NULL
    REFERENCES shrimp_bilibili_certification_integrity_audits(id) ON DELETE RESTRICT,
  current_certification_id uuid
    REFERENCES shrimp_bilibili_post_restore_certifications(id) ON DELETE RESTRICT,
  proof_snapshot jsonb NOT NULL,
  proof_snapshot_sha256 char(64) NOT NULL,
  proof_sha256 char(64) NOT NULL UNIQUE,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(proof_snapshot)='object'),
  CHECK (char_length(proof_snapshot_sha256)=64),
  CHECK (char_length(proof_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_cert_audit_proofs
  ON shrimp_bilibili_certification_audit_proofs(generated_at DESC);

CREATE OR REPLACE FUNCTION prevent_bilibili_cert_integrity_audit_mutation()
RETURNS trigger AS $integrity_audit$
BEGIN
  RAISE EXCEPTION 'Certification integrity audit is immutable';
END;
$integrity_audit$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_cert_integrity_audit_mutation
  ON shrimp_bilibili_certification_integrity_audits;
CREATE TRIGGER trg_prevent_bilibili_cert_integrity_audit_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_certification_integrity_audits
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_cert_integrity_audit_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_cert_audit_proof_mutation()
RETURNS trigger AS $audit_proof$
BEGIN
  RAISE EXCEPTION 'Certification audit proof is immutable';
END;
$audit_proof$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_cert_audit_proof_mutation
  ON shrimp_bilibili_certification_audit_proofs;
CREATE TRIGGER trg_prevent_bilibili_cert_audit_proof_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_certification_audit_proofs
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_cert_audit_proof_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_renewal_escalation_evidence_mutation()
RETURNS trigger AS $renewal_escalation$
BEGIN
  IF OLD.escalation_status='OPEN'
     AND NEW.escalation_status IN ('CLOSED','SUPERSEDED')
     AND NEW.certification_id=OLD.certification_id
     AND NEW.escalation_type=OLD.escalation_type
     AND NEW.severity=OLD.severity
     AND NEW.due_at=OLD.due_at
     AND NEW.breached_at=OLD.breached_at
     AND NEW.overdue_minutes=OLD.overdue_minutes
     AND NEW.evidence_snapshot=OLD.evidence_snapshot
     AND NEW.evidence_sha256=OLD.evidence_sha256
     AND NEW.escalation_sha256=OLD.escalation_sha256
  THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Renewal SLA escalation evidence is immutable';
END;
$renewal_escalation$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_renewal_escalation_evidence_mutation
  ON shrimp_bilibili_renewal_sla_escalations;
CREATE TRIGGER trg_prevent_bilibili_renewal_escalation_evidence_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_renewal_sla_escalations
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_renewal_escalation_evidence_mutation();
