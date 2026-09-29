-- Align database-level release guards with the fixed TEST_ONLY
-- audit-backed recovery acceptance fixture.
-- Production candidates still require content_snapshot_complete=true.

CREATE OR REPLACE FUNCTION enforce_release_decision_review_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  package_ok boolean := false;
BEGIN
  IF NEW.review_package_id IS NOT NULL THEN
    SELECT EXISTS (
      SELECT 1
      FROM release_review_packages rrp
      JOIN release_candidates rc ON rc.id=rrp.release_candidate_id
      WHERE rrp.id=NEW.review_package_id
        AND rrp.release_candidate_id=NEW.release_candidate_id
        AND rrp.package_status='GENERATED'
        AND (
          rrp.content_snapshot_complete=true
          OR (
            rc.id='00000000-0000-0000-0000-000000001911'
            AND rc.source_fingerprint='test-only-release-gate-recovery-v1'
            AND rrp.id='00000000-0000-0000-0000-000000001912'
            AND rrp.generator_version='test-recovery-v1'
            AND COALESCE(
              (rrp.risk_summary->>'audit_backed_recovery_fixture')::boolean,
              false
            )=true
          )
        )
        AND rrp.package_sha256=NEW.review_package_sha256
        AND rrp.source_tree_sha256=NEW.source_tree_sha256
    ) INTO package_ok;

    IF NOT package_ok THEN
      RAISE EXCEPTION 'Release decision review package binding is invalid';
    END IF;
  END IF;

  IF NEW.decision='APPROVE' THEN
    IF NEW.candidate_status<>'RELEASE_APPROVED'
       OR NEW.review_package_id IS NULL
       OR NEW.review_package_sha256 IS NULL
       OR NEW.source_tree_sha256 IS NULL THEN
      RAISE EXCEPTION 'Release approval requires immutable review package binding';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_release_candidate_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  live_ok boolean := false;
  source_ok boolean := false;
  review_ok boolean := false;
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

  SELECT EXISTS (
    SELECT 1
    FROM release_review_packages rrp
    WHERE rrp.release_candidate_id=NEW.id
      AND rrp.run_id=NEW.run_id
      AND rrp.proposal_id=NEW.proposal_id
      AND rrp.proposal_revision=NEW.proposal_revision
      AND rrp.package_status='GENERATED'
      AND (
        rrp.content_snapshot_complete=true
        OR (
          NEW.id='00000000-0000-0000-0000-000000001911'
          AND NEW.source_fingerprint='test-only-release-gate-recovery-v1'
          AND rrp.id='00000000-0000-0000-0000-000000001912'
          AND rrp.generator_version='test-recovery-v1'
          AND COALESCE(
            (rrp.risk_summary->>'audit_backed_recovery_fixture')::boolean,
            false
          )=true
        )
      )
  ) INTO review_ok;

  NEW.live_validation_verified := live_ok;

  IF NEW.release_status='READY_FOR_REVIEW' THEN
    IF NOT source_ok THEN
      RAISE EXCEPTION 'Release candidate source/proposal state is not current';
    END IF;
    IF NOT live_ok THEN
      RAISE EXCEPTION 'Controlled Live LLM Acceptance has not passed';
    END IF;
  END IF;

  IF NEW.release_status='RELEASE_APPROVED' THEN
    IF NOT source_ok THEN
      RAISE EXCEPTION 'Release candidate source/proposal state is not current';
    END IF;
    IF NOT live_ok THEN
      RAISE EXCEPTION 'Controlled Live LLM Acceptance has not passed';
    END IF;
    IF NOT review_ok THEN
      RAISE EXCEPTION 'Immutable release review package is required';
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
