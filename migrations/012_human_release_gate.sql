CREATE TABLE IF NOT EXISTS release_candidates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL UNIQUE REFERENCES sandbox_build_requests(id) ON DELETE RESTRICT,
  run_id uuid NOT NULL REFERENCES sandbox_runs(id) ON DELETE RESTRICT,
  proposal_id uuid NOT NULL REFERENCES build_proposals(id) ON DELETE RESTRICT,
  proposal_revision integer NOT NULL,
  source_fingerprint text NOT NULL,
  release_status text NOT NULL DEFAULT 'WAITING_LIVE_VALIDATION',
  live_validation_required boolean NOT NULL DEFAULT true,
  live_validation_verified boolean NOT NULL DEFAULT false,
  artifact_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
  artifact_manifest_sha256 text NOT NULL,
  test_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  deployment_enabled boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  approved_at timestamptz,
  rejected_at timestamptz,
  CONSTRAINT chk_release_status CHECK (
    release_status IN (
      'WAITING_LIVE_VALIDATION',
      'READY_FOR_REVIEW',
      'RELEASE_APPROVED',
      'RELEASE_REJECTED',
      'STALE'
    )
  ),
  CONSTRAINT chk_release_live_required CHECK (live_validation_required=true),
  CONSTRAINT chk_release_deployment_disabled CHECK (deployment_enabled=false),
  CONSTRAINT chk_release_manifest_sha CHECK (length(artifact_manifest_sha256)=64)
);

CREATE INDEX IF NOT EXISTS idx_release_candidates_status
  ON release_candidates(release_status,updated_at DESC);

CREATE TABLE IF NOT EXISTS release_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  release_candidate_id uuid NOT NULL REFERENCES release_candidates(id) ON DELETE RESTRICT,
  candidate_status text NOT NULL,
  decision text NOT NULL CHECK (decision IN ('APPROVE','REJECT')),
  reason text NOT NULL,
  actor text NOT NULL,
  decided_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_release_decisions_candidate
  ON release_decisions(release_candidate_id,decided_at DESC);

CREATE OR REPLACE FUNCTION enforce_release_candidate_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  live_ok boolean := false;
  source_ok boolean := false;
BEGIN
  IF NEW.deployment_enabled THEN
    RAISE EXCEPTION 'Release gate never enables deployment';
  END IF;

  SELECT EXISTS (
    SELECT 1
    FROM sandbox_build_requests sbr
    JOIN build_proposals bp ON bp.id=sbr.proposal_id
    WHERE sbr.id=NEW.request_id
      AND sbr.proposal_id=NEW.proposal_id
      AND sbr.proposal_revision=NEW.proposal_revision
      AND sbr.source_fingerprint=NEW.source_fingerprint
      AND bp.id=NEW.proposal_id
      AND bp.revision=NEW.proposal_revision
      AND bp.proposal_status='APPROVED'
      AND bp.execution_enabled=false
  ) INTO source_ok;

  SELECT EXISTS (
    SELECT 1
    FROM openhands_executions oe
    JOIN sandbox_build_requests sbr ON sbr.id=oe.request_id
    WHERE oe.request_id=NEW.request_id
      AND oe.run_id=NEW.run_id
      AND oe.gateway_mode='PROXY'
      AND oe.budget_status='WITHIN_BUDGET'
      AND oe.live_model_verified=true
      AND COALESCE(oe.exit_code,1)=0
      AND sbr.request_status='ARTIFACT_READY'
      AND EXISTS (
        SELECT 1 FROM sandbox_artifacts sa WHERE sa.run_id=NEW.run_id
      )
      AND EXISTS (
        SELECT 1 FROM sandbox_test_results str
        WHERE str.run_id=NEW.run_id AND str.passed=true
      )
  ) INTO live_ok;

  NEW.live_validation_verified := live_ok;

  IF NEW.release_status IN ('READY_FOR_REVIEW','RELEASE_APPROVED') THEN
    IF NOT source_ok THEN
      RAISE EXCEPTION 'Release candidate source/proposal state is not current';
    END IF;
    IF NOT live_ok THEN
      RAISE EXCEPTION 'Controlled Live LLM Acceptance has not passed';
    END IF;
  END IF;

  IF TG_OP='UPDATE'
     AND OLD.release_status IN ('RELEASE_APPROVED','RELEASE_REJECTED')
     AND NEW.release_status<>OLD.release_status THEN
    RAISE EXCEPTION 'Terminal release decision is immutable';
  END IF;

  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_release_candidate_guard ON release_candidates;
CREATE TRIGGER trg_release_candidate_guard
BEFORE INSERT OR UPDATE ON release_candidates
FOR EACH ROW
EXECUTE FUNCTION enforce_release_candidate_guard();
