-- Step 10B.18 — Post-Restore Reliability Certification + Stability Baseline + Reopen Trigger

CREATE TABLE IF NOT EXISTS shrimp_bilibili_post_restore_certifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  certification_key text NOT NULL UNIQUE,
  observation_session_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_post_unfreeze_observation_sessions(id) ON DELETE RESTRICT,
  restore_acceptance_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_restore_acceptances(id) ON DELETE RESTRICT,
  certification_status text NOT NULL DEFAULT 'CERTIFIED',
  certification_snapshot jsonb NOT NULL,
  stability_baseline jsonb NOT NULL,
  promoted_slo jsonb NOT NULL,
  reopen_policy jsonb NOT NULL,
  certification_sha256 char(64) NOT NULL UNIQUE,
  baseline_sha256 char(64) NOT NULL,
  generated_by text NOT NULL,
  certified_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CHECK (certification_status IN ('CERTIFIED','REOPEN_RECOMMENDED','SUPERSEDED')),
  CHECK (jsonb_typeof(certification_snapshot)='object'),
  CHECK (jsonb_typeof(stability_baseline)='object'),
  CHECK (jsonb_typeof(promoted_slo)='object'),
  CHECK (jsonb_typeof(reopen_policy)='object'),
  CHECK (char_length(certification_sha256)=64),
  CHECK (char_length(baseline_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_certification_status
  ON shrimp_bilibili_post_restore_certifications(
    certification_status,certified_at DESC
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_certification_reopen_evaluations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  certification_id uuid NOT NULL
    REFERENCES shrimp_bilibili_post_restore_certifications(id) ON DELETE RESTRICT,
  evaluation_status text NOT NULL,
  trigger_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
  current_evidence jsonb NOT NULL,
  current_evidence_sha256 char(64) NOT NULL,
  evaluation_sha256 char(64) NOT NULL UNIQUE,
  evaluated_by text NOT NULL,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (evaluation_status IN ('STABLE','REOPEN_RECOMMENDED')),
  CHECK (jsonb_typeof(trigger_codes)='array'),
  CHECK (jsonb_typeof(current_evidence)='object'),
  CHECK (char_length(current_evidence_sha256)=64),
  CHECK (char_length(evaluation_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_cert_reopen_eval
  ON shrimp_bilibili_certification_reopen_evaluations(
    certification_id,evaluated_at DESC
  );

CREATE TABLE IF NOT EXISTS shrimp_bilibili_certification_reopen_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  certification_id uuid NOT NULL
    REFERENCES shrimp_bilibili_post_restore_certifications(id) ON DELETE RESTRICT,
  evaluation_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_certification_reopen_evaluations(id) ON DELETE RESTRICT,
  event_status text NOT NULL DEFAULT 'OPEN',
  trigger_codes jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  event_sha256 char(64) NOT NULL UNIQUE,
  opened_by text NOT NULL,
  opened_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz,
  CHECK (event_status IN ('OPEN','CLOSED','SUPERSEDED')),
  CHECK (jsonb_typeof(trigger_codes)='array'),
  CHECK (char_length(evidence_sha256)=64),
  CHECK (char_length(event_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_open_cert_reopen_event
  ON shrimp_bilibili_certification_reopen_events(certification_id)
  WHERE event_status='OPEN';

CREATE OR REPLACE FUNCTION prevent_bilibili_certification_mutation()
RETURNS trigger AS $$
BEGIN
  IF OLD.certification_status IN ('CERTIFIED','REOPEN_RECOMMENDED')
     AND (
       NEW.certification_status='SUPERSEDED'
       OR (
         OLD.certification_status='CERTIFIED'
         AND NEW.certification_status='REOPEN_RECOMMENDED'
       )
     )
     AND NEW.certification_snapshot=OLD.certification_snapshot
     AND NEW.stability_baseline=OLD.stability_baseline
     AND NEW.promoted_slo=OLD.promoted_slo
     AND NEW.reopen_policy=OLD.reopen_policy
     AND NEW.certification_sha256=OLD.certification_sha256
     AND NEW.baseline_sha256=OLD.baseline_sha256
  THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Post-restore reliability certification evidence is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_certification_mutation
  ON shrimp_bilibili_post_restore_certifications;
CREATE TRIGGER trg_prevent_bilibili_certification_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_post_restore_certifications
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_certification_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_cert_reopen_eval_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Certification reopen evaluation is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_cert_reopen_eval_mutation
  ON shrimp_bilibili_certification_reopen_evaluations;
CREATE TRIGGER trg_prevent_bilibili_cert_reopen_eval_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_certification_reopen_evaluations
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_cert_reopen_eval_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_cert_reopen_event_mutation()
RETURNS trigger AS $$
BEGIN
  IF OLD.event_status='OPEN'
     AND NEW.event_status IN ('CLOSED','SUPERSEDED')
     AND NEW.certification_id=OLD.certification_id
     AND NEW.evaluation_id=OLD.evaluation_id
     AND NEW.trigger_codes=OLD.trigger_codes
     AND NEW.evidence_sha256=OLD.evidence_sha256
     AND NEW.event_sha256=OLD.event_sha256
  THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Certification reopen event evidence is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_cert_reopen_event_mutation
  ON shrimp_bilibili_certification_reopen_events;
CREATE TRIGGER trg_prevent_bilibili_cert_reopen_event_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_certification_reopen_events
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_cert_reopen_event_mutation();
