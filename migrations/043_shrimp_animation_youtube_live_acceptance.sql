-- Shrimp Animation Provider v0.1 — Step 10A
-- Sacrificial YouTube Live Publisher Acceptance audit.

CREATE TABLE IF NOT EXISTS shrimp_animation_youtube_live_acceptance_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_animation_publish_executions(id) ON DELETE CASCADE,

  acceptance_status text NOT NULL DEFAULT 'CREATED'
    CHECK (acceptance_status IN (
      'CREATED',
      'PRECHECKED',
      'UPLOADED',
      'PUBLISHED',
      'VERIFIED_PRIVATE',
      'CLEANUP_UNKNOWN',
      'CLEANED_UP',
      'FAILED'
    )),

  expected_channel_id text NOT NULL,
  actual_channel_id text,
  reconciliation_marker text NOT NULL,
  video_id text,
  provider_video_url text,

  privacy_status text,
  processing_status text,
  provider_read_back_verified boolean NOT NULL DEFAULT false,
  private_visibility_verified boolean NOT NULL DEFAULT false,

  cleanup_write_count integer NOT NULL DEFAULT 0
    CHECK (cleanup_write_count BETWEEN 0 AND 1),
  cleanup_outcome text NOT NULL DEFAULT 'NOT_ATTEMPTED'
    CHECK (cleanup_outcome IN (
      'NOT_ATTEMPTED',
      'REQUESTED',
      'ACCEPTED',
      'AMBIGUOUS',
      'RECONCILED_DELETED',
      'RECONCILED_PRESENT',
      'REJECTED'
    )),
  cleanup_verified boolean NOT NULL DEFAULT false,
  cleanup_performed boolean NOT NULL DEFAULT false,

  external_upload_performed boolean NOT NULL DEFAULT false,
  external_publish_performed boolean NOT NULL DEFAULT false,
  production_account_touched boolean NOT NULL DEFAULT false
    CHECK (production_account_touched=false),
  public_visibility_observed boolean NOT NULL DEFAULT false
    CHECK (public_visibility_observed=false),

  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  failure_type text,
  failure_evidence_sha256 char(64),

  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,

  CHECK (char_length(expected_channel_id)>=1),
  CHECK (char_length(reconciliation_marker)>=16),
  CHECK (
    failure_evidence_sha256 IS NULL
    OR char_length(failure_evidence_sha256)=64
  )
);

CREATE INDEX IF NOT EXISTS idx_shrimp_youtube_live_acceptance_status
  ON shrimp_animation_youtube_live_acceptance_runs(
    acceptance_status,created_at DESC
  );

CREATE OR REPLACE FUNCTION enforce_shrimp_youtube_live_acceptance_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $youtube_acceptance$
BEGIN
  IF NEW.production_account_touched THEN
    RAISE EXCEPTION 'Step 10A must never touch a Production/main YouTube account';
  END IF;

  IF NEW.public_visibility_observed THEN
    RAISE EXCEPTION 'Step 10A must never observe public visibility';
  END IF;

  IF NEW.cleanup_write_count<0 OR NEW.cleanup_write_count>1 THEN
    RAISE EXCEPTION 'Step 10A cleanup provider-write count must be zero or one';
  END IF;

  IF TG_OP='UPDATE' THEN
    IF NEW.id<>OLD.id
       OR NEW.execution_id<>OLD.execution_id
       OR NEW.expected_channel_id<>OLD.expected_channel_id
       OR NEW.reconciliation_marker<>OLD.reconciliation_marker
       OR NEW.created_by<>OLD.created_by
       OR NEW.created_at<>OLD.created_at THEN
      RAISE EXCEPTION 'Step 10A acceptance identity is immutable';
    END IF;

    IF OLD.actual_channel_id IS NOT NULL
       AND NEW.actual_channel_id IS DISTINCT FROM OLD.actual_channel_id THEN
      RAISE EXCEPTION 'Step 10A authenticated channel binding is immutable';
    END IF;

    IF OLD.video_id IS NOT NULL
       AND NEW.video_id IS DISTINCT FROM OLD.video_id THEN
      RAISE EXCEPTION 'Step 10A provider video ID is immutable';
    END IF;

    IF OLD.provider_video_url IS NOT NULL
       AND NEW.provider_video_url IS DISTINCT FROM OLD.provider_video_url THEN
      RAISE EXCEPTION 'Step 10A provider video URL is immutable';
    END IF;

    IF NEW.cleanup_write_count<OLD.cleanup_write_count THEN
      RAISE EXCEPTION 'Step 10A cleanup write count is monotonic';
    END IF;

    IF OLD.cleanup_verified AND NOT NEW.cleanup_verified THEN
      RAISE EXCEPTION 'Step 10A cleanup verification is monotonic';
    END IF;

    IF OLD.external_upload_performed AND NOT NEW.external_upload_performed THEN
      RAISE EXCEPTION 'Step 10A upload evidence is monotonic';
    END IF;

    IF OLD.external_publish_performed AND NOT NEW.external_publish_performed THEN
      RAISE EXCEPTION 'Step 10A publish evidence is monotonic';
    END IF;
  END IF;

  NEW.updated_at=now();
  RETURN NEW;
END;
$youtube_acceptance$;

DROP TRIGGER IF EXISTS trg_shrimp_youtube_live_acceptance_guard
  ON shrimp_animation_youtube_live_acceptance_runs;
CREATE TRIGGER trg_shrimp_youtube_live_acceptance_guard
BEFORE INSERT OR UPDATE ON shrimp_animation_youtube_live_acceptance_runs
FOR EACH ROW EXECUTE FUNCTION enforce_shrimp_youtube_live_acceptance_guard();

COMMENT ON TABLE shrimp_animation_youtube_live_acceptance_runs IS
  'Step 10A sacrificial YouTube private-upload acceptance evidence. Public visibility and Production/main account access are forbidden.';
