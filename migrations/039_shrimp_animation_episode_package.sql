ALTER TABLE shrimp_animation_jobs
  ADD COLUMN IF NOT EXISTS episode_bundle_sha256 char(64),
  ADD COLUMN IF NOT EXISTS release_review_package_sha256 char(64),
  ADD COLUMN IF NOT EXISTS review_status text NOT NULL DEFAULT 'NOT_READY';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_episode_bundle_sha256'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_episode_bundle_sha256
      CHECK (
        episode_bundle_sha256 IS NULL
        OR char_length(episode_bundle_sha256)=64
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_release_review_package_sha256'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_release_review_package_sha256
      CHECK (
        release_review_package_sha256 IS NULL
        OR char_length(release_review_package_sha256)=64
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_shrimp_review_status'
      AND conrelid='shrimp_animation_jobs'::regclass
  ) THEN
    ALTER TABLE shrimp_animation_jobs
      ADD CONSTRAINT chk_shrimp_review_status
      CHECK (
        review_status IN (
          'NOT_READY','READY_FOR_HUMAN_REVIEW','STALE'
        )
      );
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS shrimp_animation_episode_bundles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  render_id uuid NOT NULL
    REFERENCES shrimp_animation_renders(id) ON DELETE RESTRICT,
  qc_report_id uuid NOT NULL
    REFERENCES shrimp_animation_qc_reports(id) ON DELETE RESTRICT,
  episode_id text NOT NULL,
  packaging_version text NOT NULL,
  animation_manifest_sha256 char(64) NOT NULL,
  assets_manifest_sha256 char(64) NOT NULL,
  voices_manifest_sha256 char(64) NOT NULL,
  render_artifact_sha256 char(64) NOT NULL,
  qc_report_sha256 char(64) NOT NULL,
  bundle_manifest_sha256 char(64) NOT NULL,
  bundle_uri text NOT NULL,
  bundle_sha256 char(64) NOT NULL,
  byte_size bigint NOT NULL,
  artifact_manifest jsonb NOT NULL,
  bundle_manifest jsonb NOT NULL,
  bundle_status text NOT NULL DEFAULT 'CURRENT',
  created_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CHECK (char_length(animation_manifest_sha256)=64),
  CHECK (char_length(assets_manifest_sha256)=64),
  CHECK (char_length(voices_manifest_sha256)=64),
  CHECK (char_length(render_artifact_sha256)=64),
  CHECK (char_length(qc_report_sha256)=64),
  CHECK (char_length(bundle_manifest_sha256)=64),
  CHECK (char_length(bundle_sha256)=64),
  CHECK (byte_size > 0),
  CHECK (bundle_status IN ('CURRENT','STALE'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_animation_current_episode_bundle
  ON shrimp_animation_episode_bundles(provider_job_id)
  WHERE bundle_status='CURRENT';

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_episode_bundles_job
  ON shrimp_animation_episode_bundles(provider_job_id,created_at DESC);

CREATE TABLE IF NOT EXISTS shrimp_animation_release_review_packages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  bundle_id uuid NOT NULL
    REFERENCES shrimp_animation_episode_bundles(id) ON DELETE RESTRICT,
  generator_version text NOT NULL,
  review_status text NOT NULL DEFAULT 'READY_FOR_HUMAN_REVIEW',
  package_sha256 char(64) NOT NULL,
  package_content jsonb NOT NULL,
  review_document_uri text NOT NULL,
  review_document_sha256 char(64) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CHECK (char_length(package_sha256)=64),
  CHECK (char_length(review_document_sha256)=64),
  CHECK (review_status IN ('READY_FOR_HUMAN_REVIEW','STALE'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_animation_current_review_package
  ON shrimp_animation_release_review_packages(provider_job_id)
  WHERE review_status='READY_FOR_HUMAN_REVIEW';

CREATE INDEX IF NOT EXISTS idx_shrimp_animation_review_packages_job
  ON shrimp_animation_release_review_packages(provider_job_id,created_at DESC);

CREATE OR REPLACE FUNCTION prevent_shrimp_episode_bundle_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.provider_job_id IS DISTINCT FROM OLD.provider_job_id
     OR NEW.render_id IS DISTINCT FROM OLD.render_id
     OR NEW.qc_report_id IS DISTINCT FROM OLD.qc_report_id
     OR NEW.episode_id IS DISTINCT FROM OLD.episode_id
     OR NEW.packaging_version IS DISTINCT FROM OLD.packaging_version
     OR NEW.animation_manifest_sha256 IS DISTINCT FROM OLD.animation_manifest_sha256
     OR NEW.assets_manifest_sha256 IS DISTINCT FROM OLD.assets_manifest_sha256
     OR NEW.voices_manifest_sha256 IS DISTINCT FROM OLD.voices_manifest_sha256
     OR NEW.render_artifact_sha256 IS DISTINCT FROM OLD.render_artifact_sha256
     OR NEW.qc_report_sha256 IS DISTINCT FROM OLD.qc_report_sha256
     OR NEW.bundle_manifest_sha256 IS DISTINCT FROM OLD.bundle_manifest_sha256
     OR NEW.bundle_uri IS DISTINCT FROM OLD.bundle_uri
     OR NEW.bundle_sha256 IS DISTINCT FROM OLD.bundle_sha256
     OR NEW.byte_size IS DISTINCT FROM OLD.byte_size
     OR NEW.artifact_manifest IS DISTINCT FROM OLD.artifact_manifest
     OR NEW.bundle_manifest IS DISTINCT FROM OLD.bundle_manifest
  THEN
    RAISE EXCEPTION 'Shrimp episode bundle identity/content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_episode_bundle_immutable
  ON shrimp_animation_episode_bundles;
CREATE TRIGGER trg_shrimp_episode_bundle_immutable
BEFORE UPDATE ON shrimp_animation_episode_bundles
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_episode_bundle_mutation();

CREATE OR REPLACE FUNCTION prevent_shrimp_review_package_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.provider_job_id IS DISTINCT FROM OLD.provider_job_id
     OR NEW.bundle_id IS DISTINCT FROM OLD.bundle_id
     OR NEW.generator_version IS DISTINCT FROM OLD.generator_version
     OR NEW.package_sha256 IS DISTINCT FROM OLD.package_sha256
     OR NEW.package_content IS DISTINCT FROM OLD.package_content
     OR NEW.review_document_uri IS DISTINCT FROM OLD.review_document_uri
     OR NEW.review_document_sha256 IS DISTINCT FROM OLD.review_document_sha256
  THEN
    RAISE EXCEPTION 'Shrimp release review package content is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shrimp_review_package_immutable
  ON shrimp_animation_release_review_packages;
CREATE TRIGGER trg_shrimp_review_package_immutable
BEFORE UPDATE ON shrimp_animation_release_review_packages
FOR EACH ROW EXECUTE FUNCTION prevent_shrimp_review_package_mutation();

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

    UPDATE shrimp_animation_jobs
    SET episode_bundle_sha256=NULL,
        release_review_package_sha256=NULL,
        review_status='STALE',
        updated_at=now()
    WHERE provider_job_id=NEW.provider_job_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_stale_shrimp_package_with_qc
  ON shrimp_animation_qc_reports;
CREATE TRIGGER trg_stale_shrimp_package_with_qc
AFTER UPDATE OF qc_status ON shrimp_animation_qc_reports
FOR EACH ROW EXECUTE FUNCTION stale_shrimp_package_when_qc_stales();
