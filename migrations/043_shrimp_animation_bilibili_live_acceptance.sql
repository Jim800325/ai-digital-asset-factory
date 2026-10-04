-- Shrimp Animation Provider v0.1 — Step 10B
-- Sacrificial Bilibili live acceptance audit.
-- The acceptance is private-only (is_only_self=1), sacrificial-account bound,
-- and cleanup is separately bounded to one provider delete mutation.

CREATE TABLE IF NOT EXISTS shrimp_animation_bilibili_live_acceptance_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_animation_publish_executions(id) ON DELETE CASCADE,

  acceptance_status text NOT NULL DEFAULT 'CREATED',
  expected_mid text NOT NULL,
  actual_mid text,
  reconciliation_marker text NOT NULL,

  aid bigint,
  bvid text,
  provider_video_url text,

  archive_state text,
  is_only_self integer,
  provider_read_back_verified boolean NOT NULL DEFAULT false,
  private_visibility_verified boolean NOT NULL DEFAULT false,

  cleanup_write_count integer NOT NULL DEFAULT 0,
  cleanup_outcome text NOT NULL DEFAULT 'NOT_ATTEMPTED',
  cleanup_verified boolean NOT NULL DEFAULT false,
  cleanup_performed boolean NOT NULL DEFAULT false,

  external_upload_performed boolean NOT NULL DEFAULT false,
  external_publish_performed boolean NOT NULL DEFAULT false,
  production_account_touched boolean NOT NULL DEFAULT false,
  public_visibility_observed boolean NOT NULL DEFAULT false,

  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  failure_type text,
  failure_evidence_sha256 char(64),

  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,

  CHECK (
    acceptance_status IN (
      'CREATED',
      'PRECHECKED',
      'UPLOADED',
      'PUBLISHED',
      'VERIFIED_PRIVATE',
      'CLEANUP_UNKNOWN',
      'CLEANED_UP',
      'FAILED'
    )
  ),
  CHECK (char_length(expected_mid)>=1),
  CHECK (
    actual_mid IS NULL OR char_length(actual_mid)>=1
  ),
  CHECK (char_length(reconciliation_marker)>=16),
  CHECK (
    bvid IS NULL OR char_length(bvid)>=4
  ),
  CHECK (
    is_only_self IS NULL OR is_only_self IN (0,1)
  ),
  CHECK (cleanup_write_count BETWEEN 0 AND 1),
  CHECK (
    cleanup_outcome IN (
      'NOT_ATTEMPTED',
      'REQUESTED',
      'ACCEPTED',
      'AMBIGUOUS',
      'RECONCILED_DELETED',
      'RECONCILED_PRESENT',
      'REJECTED'
    )
  ),
  CHECK (
    failure_evidence_sha256 IS NULL
    OR char_length(failure_evidence_sha256)=64
  ),
  CHECK (production_account_touched=false),
  CHECK (public_visibility_observed=false),
  CHECK (
    acceptance_status<>'CLEANED_UP'
    OR (
      provider_read_back_verified=true
      AND private_visibility_verified=true
      AND cleanup_verified=true
      AND cleanup_performed=true
      AND cleanup_write_count=1
      AND is_only_self=1
    )
  )
);

CREATE INDEX IF NOT EXISTS idx_shrimp_bilibili_live_acceptance_status
  ON shrimp_animation_bilibili_live_acceptance_runs(
    acceptance_status,created_at DESC
  );

CREATE OR REPLACE FUNCTION enforce_shrimp_bilibili_live_acceptance_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $bili_acceptance$
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.acceptance_status<>'CREATED'
       OR NEW.cleanup_write_count<>0
       OR NEW.cleanup_outcome<>'NOT_ATTEMPTED'
       OR NEW.provider_read_back_verified
       OR NEW.private_visibility_verified
       OR NEW.cleanup_verified
       OR NEW.cleanup_performed
       OR NEW.production_account_touched
       OR NEW.public_visibility_observed THEN
      RAISE EXCEPTION 'Bilibili live acceptance must begin as untouched CREATED audit';
    END IF;
  ELSE
    IF NEW.execution_id<>OLD.execution_id
       OR NEW.expected_mid<>OLD.expected_mid
       OR NEW.reconciliation_marker<>OLD.reconciliation_marker
       OR NEW.created_by<>OLD.created_by
       OR NEW.created_at<>OLD.created_at THEN
      RAISE EXCEPTION 'Bilibili live acceptance identity is immutable';
    END IF;

    IF OLD.actual_mid IS NOT NULL
       AND NEW.actual_mid IS DISTINCT FROM OLD.actual_mid THEN
      RAISE EXCEPTION 'Bilibili authenticated MID is immutable';
    END IF;
    IF OLD.aid IS NOT NULL AND NEW.aid IS DISTINCT FROM OLD.aid THEN
      RAISE EXCEPTION 'Bilibili acceptance AID is immutable';
    END IF;
    IF OLD.bvid IS NOT NULL AND NEW.bvid IS DISTINCT FROM OLD.bvid THEN
      RAISE EXCEPTION 'Bilibili acceptance BVID is immutable';
    END IF;
    IF OLD.provider_video_url IS NOT NULL
       AND NEW.provider_video_url IS DISTINCT FROM OLD.provider_video_url THEN
      RAISE EXCEPTION 'Bilibili acceptance provider URL is immutable';
    END IF;

    IF NEW.cleanup_write_count < OLD.cleanup_write_count
       OR NEW.cleanup_write_count > 1 THEN
      RAISE EXCEPTION 'Bilibili cleanup write count is monotonic and bounded to one';
    END IF;

    IF OLD.provider_read_back_verified
       AND NOT NEW.provider_read_back_verified THEN
      RAISE EXCEPTION 'Bilibili read-back evidence is monotonic';
    END IF;
    IF OLD.private_visibility_verified
       AND NOT NEW.private_visibility_verified THEN
      RAISE EXCEPTION 'Bilibili private visibility evidence is monotonic';
    END IF;
    IF OLD.cleanup_verified AND NOT NEW.cleanup_verified THEN
      RAISE EXCEPTION 'Bilibili cleanup verification is monotonic';
    END IF;
    IF OLD.cleanup_performed AND NOT NEW.cleanup_performed THEN
      RAISE EXCEPTION 'Bilibili cleanup evidence is monotonic';
    END IF;
    IF NEW.production_account_touched THEN
      RAISE EXCEPTION 'Bilibili Production/main account access is forbidden';
    END IF;
    IF NEW.public_visibility_observed THEN
      RAISE EXCEPTION 'Bilibili public visibility is forbidden in Step 10B';
    END IF;
  END IF;

  NEW.updated_at=now();
  RETURN NEW;
END;
$bili_acceptance$;

DROP TRIGGER IF EXISTS trg_shrimp_bilibili_live_acceptance_guard
  ON shrimp_animation_bilibili_live_acceptance_runs;
CREATE TRIGGER trg_shrimp_bilibili_live_acceptance_guard
BEFORE INSERT OR UPDATE ON shrimp_animation_bilibili_live_acceptance_runs
FOR EACH ROW EXECUTE FUNCTION enforce_shrimp_bilibili_live_acceptance_guard();

COMMENT ON TABLE shrimp_animation_bilibili_live_acceptance_runs IS
  'Step 10B sacrificial Bilibili private-only live acceptance audit.';
