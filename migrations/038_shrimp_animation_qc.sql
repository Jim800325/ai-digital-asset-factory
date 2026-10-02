ALTER TABLE shrimp_animation_jobs
  ADD COLUMN IF NOT EXISTS qc_report_sha256 char(64);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_qc_report_sha256'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_qc_report_sha256
      CHECK (
        qc_report_sha256 IS NULL
        OR char_length(qc_report_sha256)=64
      );
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS shrimp_animation_qc_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  render_id uuid NOT NULL
    REFERENCES shrimp_animation_renders(id) ON DELETE RESTRICT,
  render_artifact_sha256 char(64) NOT NULL,
  animation_manifest_sha256 char(64) NOT NULL,
  analyzer_version text NOT NULL,
  report_sha256 char(64) NOT NULL,
  report_content jsonb NOT NULL,
  passed boolean NOT NULL,
  hard_failure_count integer NOT NULL,
  qc_status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CHECK (char_length(render_artifact_sha256)=64),
  CHECK (char_length(animation_manifest_sha256)=64),
  CHECK (char_length(report_sha256)=64),
  CHECK (hard_failure_count >= 0),
  CHECK (
    (passed=true AND hard_failure_count=0 AND qc_status IN ('PASSED','STALE'))
    OR
    (passed=false AND hard_failure_count>0 AND qc_status='FAILED')
  ),
  CHECK (qc_status IN ('PASSED','FAILED','STALE'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_animation_current_qc_pass
  ON shrimp_animation_qc_reports(provider_job_id)
  WHERE qc_status='PASSED';

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_qc_reports_job
  ON shrimp_animation_qc_reports(provider_job_id,created_at DESC);

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_qc_reports_render
  ON shrimp_animation_qc_reports(render_id);

CREATE OR REPLACE FUNCTION prevent_shrimp_animation_qc_report_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.provider_job_id IS DISTINCT FROM OLD.provider_job_id
     OR NEW.render_id IS DISTINCT FROM OLD.render_id
     OR NEW.render_artifact_sha256 IS DISTINCT FROM OLD.render_artifact_sha256
     OR NEW.animation_manifest_sha256 IS DISTINCT FROM OLD.animation_manifest_sha256
     OR NEW.analyzer_version IS DISTINCT FROM OLD.analyzer_version
     OR NEW.report_sha256 IS DISTINCT FROM OLD.report_sha256
     OR NEW.report_content IS DISTINCT FROM OLD.report_content
     OR NEW.passed IS DISTINCT FROM OLD.passed
     OR NEW.hard_failure_count IS DISTINCT FROM OLD.hard_failure_count
  THEN
    RAISE EXCEPTION 'Shrimp animation QC report identity/content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_animation_qc_report_immutable
  ON shrimp_animation_qc_reports;
CREATE TRIGGER trg_shrimp_animation_qc_report_immutable
BEFORE UPDATE ON shrimp_animation_qc_reports
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_animation_qc_report_mutation();

CREATE OR REPLACE FUNCTION stale_shrimp_qc_when_render_stales()
RETURNS trigger AS $$
BEGIN
  IF OLD.render_status='CURRENT'
     AND NEW.render_status='STALE'
  THEN
    UPDATE shrimp_animation_qc_reports
    SET qc_status='STALE',
        superseded_at=COALESCE(superseded_at,now())
    WHERE render_id=NEW.id
      AND qc_status='PASSED';

    UPDATE shrimp_animation_jobs
    SET qc_report_sha256=NULL,
        updated_at=now()
    WHERE provider_job_id=NEW.provider_job_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_stale_shrimp_qc_with_render
  ON shrimp_animation_renders;
CREATE TRIGGER trg_stale_shrimp_qc_with_render
AFTER UPDATE OF render_status ON shrimp_animation_renders
FOR EACH ROW EXECUTE FUNCTION stale_shrimp_qc_when_render_stales();
