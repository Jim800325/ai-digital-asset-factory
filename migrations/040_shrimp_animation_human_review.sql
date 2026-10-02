ALTER TABLE shrimp_animation_jobs
  ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;

ALTER TABLE shrimp_animation_jobs
  DROP CONSTRAINT IF EXISTS chk_shrimp_review_status;

ALTER TABLE shrimp_animation_jobs
  ADD CONSTRAINT chk_shrimp_review_status
  CHECK (
    review_status IN (
      'NOT_READY',
      'READY_FOR_HUMAN_REVIEW',
      'RELEASE_APPROVED',
      'RELEASE_REJECTED',
      'STALE'
    )
  );

CREATE TABLE IF NOT EXISTS shrimp_animation_review_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  bundle_id uuid NOT NULL
    REFERENCES shrimp_animation_episode_bundles(id) ON DELETE RESTRICT,
  review_package_id uuid NOT NULL
    REFERENCES shrimp_animation_release_review_packages(id) ON DELETE RESTRICT,
  decision text NOT NULL,
  reason text NOT NULL,
  actor text NOT NULL,
  episode_bundle_sha256 char(64) NOT NULL,
  release_review_package_sha256 char(64) NOT NULL,
  confirmed_checklist jsonb NOT NULL DEFAULT '[]'::jsonb,
  decision_sha256 char(64) NOT NULL,
  decision_status text NOT NULL DEFAULT 'CURRENT',
  decided_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CHECK (decision IN ('APPROVE','REJECT')),
  CHECK (char_length(reason) >= 3),
  CHECK (char_length(actor) >= 1),
  CHECK (char_length(episode_bundle_sha256)=64),
  CHECK (char_length(release_review_package_sha256)=64),
  CHECK (jsonb_typeof(confirmed_checklist)='array'),
  CHECK (char_length(decision_sha256)=64),
  CHECK (decision_status IN ('CURRENT','STALE'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_animation_current_review_decision
  ON shrimp_animation_review_decisions(provider_job_id)
  WHERE decision_status='CURRENT';

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_review_decisions_job
  ON shrimp_animation_review_decisions(provider_job_id,decided_at DESC);

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_review_decisions_package
  ON shrimp_animation_review_decisions(review_package_id);

CREATE OR REPLACE FUNCTION prevent_shrimp_review_decision_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.provider_job_id IS DISTINCT FROM OLD.provider_job_id
     OR NEW.bundle_id IS DISTINCT FROM OLD.bundle_id
     OR NEW.review_package_id IS DISTINCT FROM OLD.review_package_id
     OR NEW.decision IS DISTINCT FROM OLD.decision
     OR NEW.reason IS DISTINCT FROM OLD.reason
     OR NEW.actor IS DISTINCT FROM OLD.actor
     OR NEW.episode_bundle_sha256 IS DISTINCT FROM OLD.episode_bundle_sha256
     OR NEW.release_review_package_sha256 IS DISTINCT FROM OLD.release_review_package_sha256
     OR NEW.confirmed_checklist IS DISTINCT FROM OLD.confirmed_checklist
     OR NEW.decision_sha256 IS DISTINCT FROM OLD.decision_sha256
     OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
  THEN
    RAISE EXCEPTION 'Shrimp human review decision identity/content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_review_decision_immutable
  ON shrimp_animation_review_decisions;
CREATE TRIGGER trg_shrimp_review_decision_immutable
BEFORE UPDATE ON shrimp_animation_review_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_review_decision_mutation();

CREATE OR REPLACE FUNCTION prevent_shrimp_review_decision_delete()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Shrimp human review decisions cannot be deleted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_review_decision_no_delete
  ON shrimp_animation_review_decisions;
CREATE TRIGGER trg_shrimp_review_decision_no_delete
BEFORE DELETE ON shrimp_animation_review_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_review_decision_delete();

CREATE OR REPLACE FUNCTION stale_shrimp_package_when_qc_stales()
RETURNS trigger AS $$
BEGIN
  IF OLD.qc_status='PASSED'
     AND NEW.qc_status='STALE'
  THEN
    UPDATE shrimp_animation_episode_bundles
    SET bundle_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE qc_report_id=NEW.id
      AND bundle_status='CURRENT';

    UPDATE shrimp_animation_release_review_packages
    SET review_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE provider_job_id=NEW.provider_job_id
      AND review_status='READY_FOR_HUMAN_REVIEW';

    UPDATE shrimp_animation_review_decisions
    SET decision_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE provider_job_id=NEW.provider_job_id
      AND decision_status='CURRENT';

    UPDATE shrimp_animation_jobs
    SET episode_bundle_sha256=NULL,
        release_review_package_sha256=NULL,
        review_status='STALE',
        reviewed_at=NULL,
        updated_at=now()
    WHERE provider_job_id=NEW.provider_job_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
