CREATE TABLE IF NOT EXISTS sandbox_artifact_contents (
  artifact_id uuid PRIMARY KEY REFERENCES sandbox_artifacts(id) ON DELETE CASCADE,
  content_bytes bytea NOT NULL,
  content_sha256 text NOT NULL,
  captured_at timestamptz NOT NULL DEFAULT now(),
  CHECK (length(content_sha256)=64),
  CHECK (encode(digest(content_bytes,'sha256'),'hex')=content_sha256)
);

CREATE OR REPLACE FUNCTION validate_artifact_content_snapshot()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  expected_sha text;
  expected_size bigint;
BEGIN
  SELECT sha256,byte_size
  INTO expected_sha,expected_size
  FROM sandbox_artifacts
  WHERE id=NEW.artifact_id;

  IF expected_sha IS NULL THEN
    RAISE EXCEPTION 'Artifact metadata not found for content snapshot';
  END IF;
  IF NEW.content_sha256<>expected_sha THEN
    RAISE EXCEPTION 'Artifact content hash does not match captured metadata';
  END IF;
  IF octet_length(NEW.content_bytes)<>expected_size THEN
    RAISE EXCEPTION 'Artifact content size does not match captured metadata';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_artifact_content_snapshot ON sandbox_artifact_contents;
CREATE TRIGGER trg_validate_artifact_content_snapshot
BEFORE INSERT ON sandbox_artifact_contents
FOR EACH ROW
EXECUTE FUNCTION validate_artifact_content_snapshot();

CREATE OR REPLACE FUNCTION protect_artifact_content_snapshot()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'Artifact content snapshots are immutable';
END;
$$;

DROP TRIGGER IF EXISTS trg_artifact_content_snapshot_immutable ON sandbox_artifact_contents;
CREATE TRIGGER trg_artifact_content_snapshot_immutable
BEFORE UPDATE OR DELETE ON sandbox_artifact_contents
FOR EACH ROW
EXECUTE FUNCTION protect_artifact_content_snapshot();

CREATE OR REPLACE FUNCTION protect_captured_artifact()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM sandbox_artifact_contents
    WHERE artifact_id=OLD.id
  ) THEN
    RAISE EXCEPTION 'Captured artifact metadata is immutable';
  END IF;

  IF TG_OP='DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_captured_artifact_immutable ON sandbox_artifacts;
CREATE TRIGGER trg_captured_artifact_immutable
BEFORE UPDATE OR DELETE ON sandbox_artifacts
FOR EACH ROW
EXECUTE FUNCTION protect_captured_artifact();

CREATE TABLE IF NOT EXISTS release_review_packages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  release_candidate_id uuid NOT NULL UNIQUE REFERENCES release_candidates(id) ON DELETE RESTRICT,
  proposal_id uuid NOT NULL REFERENCES build_proposals(id) ON DELETE RESTRICT,
  proposal_revision integer NOT NULL,
  run_id uuid NOT NULL REFERENCES sandbox_runs(id) ON DELETE RESTRICT,
  baseline_package_id uuid REFERENCES release_review_packages(id) ON DELETE SET NULL,
  package_status text NOT NULL DEFAULT 'GENERATED',
  content_snapshot_complete boolean NOT NULL DEFAULT false,
  artifact_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
  artifact_diff jsonb NOT NULL DEFAULT '[]'::jsonb,
  dependency_inventory jsonb NOT NULL DEFAULT '[]'::jsonb,
  sbom jsonb NOT NULL DEFAULT '{}'::jsonb,
  test_report jsonb NOT NULL DEFAULT '{}'::jsonb,
  risk_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_tree_sha256 text NOT NULL,
  package_sha256 text NOT NULL,
  generator_version text NOT NULL DEFAULT 'release-review-v0.3-deterministic',
  generated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (package_status IN ('GENERATED','STALE')),
  CHECK (length(source_tree_sha256)=64),
  CHECK (length(package_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_release_review_packages_proposal
  ON release_review_packages(proposal_id,generated_at DESC);

CREATE OR REPLACE FUNCTION protect_release_review_package()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Release review packages are immutable';
  END IF;

  IF TG_OP='UPDATE' THEN
    IF NEW.release_candidate_id<>OLD.release_candidate_id
       OR NEW.proposal_id<>OLD.proposal_id
       OR NEW.proposal_revision<>OLD.proposal_revision
       OR NEW.run_id<>OLD.run_id
       OR NEW.baseline_package_id IS DISTINCT FROM OLD.baseline_package_id
       OR NEW.content_snapshot_complete<>OLD.content_snapshot_complete
       OR NEW.artifact_manifest<>OLD.artifact_manifest
       OR NEW.artifact_diff<>OLD.artifact_diff
       OR NEW.dependency_inventory<>OLD.dependency_inventory
       OR NEW.sbom<>OLD.sbom
       OR NEW.test_report<>OLD.test_report
       OR NEW.risk_summary<>OLD.risk_summary
       OR NEW.source_tree_sha256<>OLD.source_tree_sha256
       OR NEW.package_sha256<>OLD.package_sha256
       OR NEW.generator_version<>OLD.generator_version
       OR NEW.generated_at<>OLD.generated_at THEN
      RAISE EXCEPTION 'Release review package content is immutable';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_release_review_package_immutable ON release_review_packages;
CREATE TRIGGER trg_release_review_package_immutable
BEFORE UPDATE OR DELETE ON release_review_packages
FOR EACH ROW
EXECUTE FUNCTION protect_release_review_package();
