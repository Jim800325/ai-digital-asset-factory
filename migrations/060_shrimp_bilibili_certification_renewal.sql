-- Step 10B.19 — Certification Expiry + Baseline Renewal + Attestation History + Governance Re-Certification

DROP TRIGGER IF EXISTS trg_prevent_bilibili_certification_mutation
  ON shrimp_bilibili_post_restore_certifications;

ALTER TABLE shrimp_bilibili_post_restore_certifications
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_post_restore_certifications_certification_status_check;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  ADD CONSTRAINT shrimp_bilibili_post_restore_certifications_certification_status_check
  CHECK (
    certification_status IN (
      'CERTIFIED',
      'EXPIRING',
      'EXPIRED',
      'RECERTIFICATION_REQUIRED',
      'REOPEN_RECOMMENDED',
      'SUPERSEDED'
    )
  );

ALTER TABLE shrimp_bilibili_post_restore_certifications
  ADD COLUMN IF NOT EXISTS valid_from timestamptz;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  ADD COLUMN IF NOT EXISTS renewal_due_at timestamptz;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  ADD COLUMN IF NOT EXISTS previous_certification_id uuid;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  ADD COLUMN IF NOT EXISTS attestation_sequence integer;

ALTER TABLE shrimp_bilibili_post_restore_certifications
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_post_restore_certifications_observation_session_id_key;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_post_restore_certifications_restore_acceptance_id_key;

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_certification_session
  ON shrimp_bilibili_post_restore_certifications(observation_session_id,certified_at DESC);
CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_certification_acceptance
  ON shrimp_bilibili_post_restore_certifications(restore_acceptance_id,certified_at DESC);

WITH ranked AS (
  SELECT id,
         ROW_NUMBER() OVER (ORDER BY certified_at,id)::integer AS sequence
  FROM shrimp_bilibili_post_restore_certifications
)
UPDATE shrimp_bilibili_post_restore_certifications c
SET valid_from=COALESCE(c.valid_from,c.certified_at),
    expires_at=COALESCE(c.expires_at,c.certified_at + interval '30 days'),
    renewal_due_at=COALESCE(c.renewal_due_at,c.certified_at + interval '23 days'),
    attestation_sequence=COALESCE(c.attestation_sequence,ranked.sequence)
FROM ranked
WHERE c.id=ranked.id
  AND (
    c.valid_from IS NULL
    OR c.expires_at IS NULL
    OR c.renewal_due_at IS NULL
    OR c.attestation_sequence IS NULL
  );

ALTER TABLE shrimp_bilibili_post_restore_certifications
  ALTER COLUMN valid_from SET NOT NULL;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  ALTER COLUMN expires_at SET NOT NULL;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  ALTER COLUMN renewal_due_at SET NOT NULL;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  ALTER COLUMN attestation_sequence SET NOT NULL;

WITH chained AS (
  SELECT id,
         LAG(id) OVER (ORDER BY certified_at,id) AS previous_id
  FROM shrimp_bilibili_post_restore_certifications
)
UPDATE shrimp_bilibili_post_restore_certifications c
SET previous_certification_id=chained.previous_id
FROM chained
WHERE c.id=chained.id
  AND c.previous_certification_id IS NULL
  AND chained.previous_id IS NOT NULL;

ALTER TABLE shrimp_bilibili_post_restore_certifications
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_post_restore_certifications_previous_certification_id_fkey;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  ADD CONSTRAINT shrimp_bilibili_post_restore_certifications_previous_certification_id_fkey
  FOREIGN KEY (previous_certification_id)
  REFERENCES shrimp_bilibili_post_restore_certifications(id)
  ON DELETE RESTRICT;

ALTER TABLE shrimp_bilibili_post_restore_certifications
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_post_restore_certifications_validity_check;
ALTER TABLE shrimp_bilibili_post_restore_certifications
  ADD CONSTRAINT shrimp_bilibili_post_restore_certifications_validity_check
  CHECK (
    expires_at>valid_from
    AND renewal_due_at>=valid_from
    AND renewal_due_at<expires_at
    AND attestation_sequence>=1
  );

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_certification_expiry
  ON shrimp_bilibili_post_restore_certifications(
    certification_status,renewal_due_at,expires_at
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_current_certification
  ON shrimp_bilibili_post_restore_certifications((1))
  WHERE certification_status<>'SUPERSEDED';

CREATE TABLE IF NOT EXISTS shrimp_bilibili_recertification_candidates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_certification_id uuid NOT NULL
    REFERENCES shrimp_bilibili_post_restore_certifications(id) ON DELETE RESTRICT,
  candidate_status text NOT NULL DEFAULT 'PENDING_APPROVAL',
  candidate_snapshot jsonb NOT NULL,
  proposed_stability_baseline jsonb NOT NULL,
  promoted_slo jsonb NOT NULL,
  reopen_policy jsonb NOT NULL,
  source_certification_sha256 char(64) NOT NULL,
  source_baseline_sha256 char(64) NOT NULL,
  new_evidence_sha256 char(64) NOT NULL,
  candidate_sha256 char(64) NOT NULL UNIQUE,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  decided_at timestamptz,
  CHECK (
    candidate_status IN (
      'PENDING_APPROVAL','APPROVED','REJECTED','STALE'
    )
  ),
  CHECK (jsonb_typeof(candidate_snapshot)='object'),
  CHECK (jsonb_typeof(proposed_stability_baseline)='object'),
  CHECK (jsonb_typeof(promoted_slo)='object'),
  CHECK (jsonb_typeof(reopen_policy)='object'),
  CHECK (char_length(source_certification_sha256)=64),
  CHECK (char_length(source_baseline_sha256)=64),
  CHECK (char_length(new_evidence_sha256)=64),
  CHECK (char_length(candidate_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_pending_recert_candidate
  ON shrimp_bilibili_recertification_candidates(source_certification_id)
  WHERE candidate_status='PENDING_APPROVAL';

CREATE TABLE IF NOT EXISTS shrimp_bilibili_recertification_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_recertification_candidates(id) ON DELETE RESTRICT,
  decision text NOT NULL,
  reason text NOT NULL,
  actor text NOT NULL,
  candidate_sha256 char(64) NOT NULL,
  decision_sha256 char(64) NOT NULL UNIQUE,
  decided_at timestamptz NOT NULL DEFAULT now(),
  CHECK (decision IN ('APPROVE','REJECT')),
  CHECK (char_length(reason)>=3),
  CHECK (char_length(candidate_sha256)=64),
  CHECK (char_length(decision_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_attestations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  certification_id uuid NOT NULL
    REFERENCES shrimp_bilibili_post_restore_certifications(id) ON DELETE RESTRICT,
  previous_attestation_id uuid
    REFERENCES shrimp_bilibili_reliability_attestations(id) ON DELETE RESTRICT,
  attestation_type text NOT NULL,
  attestation_sequence integer NOT NULL,
  attestation_status text NOT NULL,
  evidence_snapshot jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  attestation_sha256 char(64) NOT NULL UNIQUE,
  actor text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    attestation_type IN (
      'INITIAL_CERTIFICATION',
      'EXPIRY_WARNING',
      'CERTIFICATION_EXPIRED',
      'RECERTIFICATION_APPROVED',
      'RECERTIFICATION_REJECTED',
      'REOPEN_RECOMMENDED',
      'SUPERSEDED'
    )
  ),
  CHECK (
    attestation_status IN (
      'VALID','WARNING','EXPIRED','APPROVED',
      'REJECTED','REOPENED','SUPERSEDED'
    )
  ),
  CHECK (attestation_sequence>=1),
  CHECK (jsonb_typeof(evidence_snapshot)='object'),
  CHECK (char_length(evidence_sha256)=64),
  CHECK (char_length(attestation_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_attestation_chain
  ON shrimp_bilibili_reliability_attestations(
    certification_id,attestation_sequence,created_at
  );

CREATE OR REPLACE FUNCTION prevent_bilibili_recert_decision_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Re-certification decision is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_recert_decision_mutation
  ON shrimp_bilibili_recertification_decisions;
CREATE TRIGGER trg_prevent_bilibili_recert_decision_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_recertification_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_recert_decision_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_attestation_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Reliability attestation is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_attestation_mutation
  ON shrimp_bilibili_reliability_attestations;
CREATE TRIGGER trg_prevent_bilibili_attestation_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_reliability_attestations
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_attestation_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_recert_candidate_evidence_mutation()
RETURNS trigger AS $$
BEGIN
  IF OLD.candidate_status='PENDING_APPROVAL'
     AND NEW.candidate_status IN ('APPROVED','REJECTED','STALE')
     AND NEW.candidate_snapshot=OLD.candidate_snapshot
     AND NEW.proposed_stability_baseline=OLD.proposed_stability_baseline
     AND NEW.promoted_slo=OLD.promoted_slo
     AND NEW.reopen_policy=OLD.reopen_policy
     AND NEW.source_certification_sha256=OLD.source_certification_sha256
     AND NEW.source_baseline_sha256=OLD.source_baseline_sha256
     AND NEW.new_evidence_sha256=OLD.new_evidence_sha256
     AND NEW.candidate_sha256=OLD.candidate_sha256
  THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Re-certification candidate evidence is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_recert_candidate_evidence_mutation
  ON shrimp_bilibili_recertification_candidates;
CREATE TRIGGER trg_prevent_bilibili_recert_candidate_evidence_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_recertification_candidates
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_recert_candidate_evidence_mutation();


CREATE OR REPLACE FUNCTION prevent_bilibili_certification_mutation()
RETURNS trigger AS $certification_mutation$
BEGIN
  IF (
       (OLD.certification_status='CERTIFIED'
         AND NEW.certification_status IN (
           'EXPIRING','EXPIRED','REOPEN_RECOMMENDED','SUPERSEDED'
         ))
       OR
       (OLD.certification_status='EXPIRING'
         AND NEW.certification_status IN (
           'EXPIRED','REOPEN_RECOMMENDED','SUPERSEDED'
         ))
       OR
       (OLD.certification_status='EXPIRED'
         AND NEW.certification_status IN (
           'RECERTIFICATION_REQUIRED','SUPERSEDED'
         ))
       OR
       (OLD.certification_status='RECERTIFICATION_REQUIRED'
         AND NEW.certification_status='SUPERSEDED')
       OR
       (OLD.certification_status='REOPEN_RECOMMENDED'
         AND NEW.certification_status='SUPERSEDED')
     )
     AND NEW.certification_snapshot=OLD.certification_snapshot
     AND NEW.stability_baseline=OLD.stability_baseline
     AND NEW.promoted_slo=OLD.promoted_slo
     AND NEW.reopen_policy=OLD.reopen_policy
     AND NEW.certification_sha256=OLD.certification_sha256
     AND NEW.baseline_sha256=OLD.baseline_sha256
     AND NEW.valid_from=OLD.valid_from
     AND NEW.expires_at=OLD.expires_at
     AND NEW.renewal_due_at=OLD.renewal_due_at
     AND NEW.previous_certification_id IS NOT DISTINCT FROM OLD.previous_certification_id
     AND NEW.attestation_sequence=OLD.attestation_sequence
  THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Post-restore reliability certification evidence is immutable';
END;
$certification_mutation$ LANGUAGE plpgsql;

CREATE TRIGGER trg_prevent_bilibili_certification_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_post_restore_certifications
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_certification_mutation();
