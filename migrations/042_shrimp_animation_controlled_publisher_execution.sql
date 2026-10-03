-- Shrimp Animation Provider v0.1 — Step 10
-- Controlled Publisher Execution.
-- Real provider adapters remain disabled by default.
-- Upload and publish each receive exactly one provider-write budget.
-- Ambiguous outcomes require read-only reconciliation and forbid blind replay.

CREATE TABLE IF NOT EXISTS shrimp_animation_publish_executions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  publish_plan_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_animation_publish_plans(id) ON DELETE CASCADE,
  authorization_decision_id uuid NOT NULL
    REFERENCES shrimp_animation_publish_authorization_decisions(id)
    ON DELETE RESTRICT,
  provider_job_id uuid NOT NULL
    REFERENCES production_provider_jobs(id) ON DELETE CASCADE,
  target_id uuid NOT NULL
    REFERENCES shrimp_animation_publish_targets(id) ON DELETE RESTRICT,

  platform text NOT NULL,
  target_key text NOT NULL,
  account_reference text NOT NULL,

  plan_sha256 char(64) NOT NULL,
  dry_run_sha256 char(64) NOT NULL,
  authorization_decision_sha256 char(64) NOT NULL,
  review_decision_sha256 char(64) NOT NULL,
  episode_bundle_sha256 char(64) NOT NULL,
  release_review_package_sha256 char(64) NOT NULL,
  render_artifact_sha256 char(64) NOT NULL,
  target_snapshot_sha256 char(64) NOT NULL,

  publish_metadata jsonb NOT NULL,
  execution_adapter text NOT NULL,
  execution_sha256 char(64) NOT NULL,
  upload_idempotency_key char(64) NOT NULL,
  publish_idempotency_key char(64) NOT NULL,

  execution_status text NOT NULL DEFAULT 'SNAPSHOT_CREATED',

  upload_outcome text NOT NULL DEFAULT 'NOT_ATTEMPTED',
  upload_write_count integer NOT NULL DEFAULT 0,
  upload_request_sha256 char(64),
  provider_upload_id text,
  upload_provider_state text,
  upload_provider_result_sha256 char(64),
  upload_last_error_type text,
  upload_last_error_sha256 char(64),
  upload_attempted_at timestamptz,
  upload_reconciled_at timestamptz,

  publish_outcome text NOT NULL DEFAULT 'NOT_ATTEMPTED',
  publish_write_count integer NOT NULL DEFAULT 0,
  publish_request_sha256 char(64),
  provider_publish_id text,
  provider_publish_url text,
  publish_provider_state text,
  publish_provider_result_sha256 char(64),
  publish_last_error_type text,
  publish_last_error_sha256 char(64),
  publish_attempted_at timestamptz,
  publish_reconciled_at timestamptz,

  external_publish_performed boolean NOT NULL DEFAULT false,
  automatic_execution boolean NOT NULL DEFAULT false,
  source_stale boolean NOT NULL DEFAULT false,

  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CHECK (platform IN ('BILIBILI','YOUTUBE','CUSTOM')),
  CHECK (char_length(account_reference)>=1),
  CHECK (char_length(plan_sha256)=64),
  CHECK (char_length(dry_run_sha256)=64),
  CHECK (char_length(authorization_decision_sha256)=64),
  CHECK (char_length(review_decision_sha256)=64),
  CHECK (char_length(episode_bundle_sha256)=64),
  CHECK (char_length(release_review_package_sha256)=64),
  CHECK (char_length(render_artifact_sha256)=64),
  CHECK (char_length(target_snapshot_sha256)=64),
  CHECK (jsonb_typeof(publish_metadata)='object'),
  CHECK (
    execution_adapter IN (
      'MOCK',
      'BILIBILI_CONTROLLED',
      'YOUTUBE_CONTROLLED'
    )
  ),
  CHECK (char_length(execution_sha256)=64),
  CHECK (char_length(upload_idempotency_key)=64),
  CHECK (char_length(publish_idempotency_key)=64),
  CHECK (
    execution_status IN (
      'SNAPSHOT_CREATED',
      'UPLOADING',
      'UPLOAD_UNKNOWN',
      'UPLOADED',
      'UPLOAD_FAILED',
      'PUBLISHING',
      'PUBLISH_UNKNOWN',
      'PUBLISHED',
      'PUBLISH_FAILED'
    )
  ),
  CHECK (
    upload_outcome IN (
      'NOT_ATTEMPTED',
      'REQUESTED',
      'ACCEPTED',
      'AMBIGUOUS',
      'RECONCILED_PENDING',
      'RECONCILED_ACCEPTED',
      'RECONCILED_FAILED',
      'REJECTED'
    )
  ),
  CHECK (upload_write_count BETWEEN 0 AND 1),
  CHECK (
    upload_request_sha256 IS NULL
    OR char_length(upload_request_sha256)=64
  ),
  CHECK (
    upload_provider_result_sha256 IS NULL
    OR char_length(upload_provider_result_sha256)=64
  ),
  CHECK (
    upload_last_error_sha256 IS NULL
    OR char_length(upload_last_error_sha256)=64
  ),
  CHECK (
    publish_outcome IN (
      'NOT_ATTEMPTED',
      'REQUESTED',
      'ACCEPTED',
      'AMBIGUOUS',
      'RECONCILED_PENDING',
      'RECONCILED_ACCEPTED',
      'RECONCILED_FAILED',
      'REJECTED'
    )
  ),
  CHECK (publish_write_count BETWEEN 0 AND 1),
  CHECK (
    publish_request_sha256 IS NULL
    OR char_length(publish_request_sha256)=64
  ),
  CHECK (
    publish_provider_result_sha256 IS NULL
    OR char_length(publish_provider_result_sha256)=64
  ),
  CHECK (
    publish_last_error_sha256 IS NULL
    OR char_length(publish_last_error_sha256)=64
  ),
  CHECK (automatic_execution=false)
);

CREATE INDEX IF NOT EXISTS idx_shrimp_publish_executions_job
  ON shrimp_animation_publish_executions(provider_job_id,created_at DESC);

CREATE INDEX IF NOT EXISTS idx_shrimp_publish_executions_target
  ON shrimp_animation_publish_executions(target_id,created_at DESC);

CREATE TABLE IF NOT EXISTS shrimp_animation_publish_execution_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id uuid NOT NULL
    REFERENCES shrimp_animation_publish_executions(id) ON DELETE CASCADE,
  event_type text NOT NULL,
  previous_status text,
  next_status text,
  actor text NOT NULL,
  provider_result_sha256 char(64),
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (char_length(event_type)>=1),
  CHECK (char_length(actor)>=1),
  CHECK (
    provider_result_sha256 IS NULL
    OR char_length(provider_result_sha256)=64
  ),
  CHECK (jsonb_typeof(details)='object')
);

CREATE INDEX IF NOT EXISTS idx_shrimp_publish_execution_events
  ON shrimp_animation_publish_execution_events(execution_id,created_at,id);

CREATE OR REPLACE FUNCTION enforce_shrimp_publish_execution_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  source_ok boolean := false;
BEGIN
  IF NEW.automatic_execution THEN
    RAISE EXCEPTION 'Shrimp Controlled Publisher automatic execution is forbidden';
  END IF;

  IF TG_OP='INSERT' THEN
    SELECT EXISTS (
      SELECT 1
      FROM shrimp_animation_publish_plans pp
      JOIN shrimp_animation_publish_authorization_decisions pad
        ON pad.id=NEW.authorization_decision_id
       AND pad.publish_plan_id=pp.id
      JOIN shrimp_animation_review_decisions rd
        ON rd.id=pp.review_decision_id
      JOIN shrimp_animation_publish_targets pt
        ON pt.id=NEW.target_id
       AND pt.id=pp.target_id
      JOIN shrimp_animation_jobs saj
        ON saj.provider_job_id=NEW.provider_job_id
       AND saj.provider_job_id=pp.provider_job_id
      JOIN production_provider_jobs pj
        ON pj.id=saj.provider_job_id
      WHERE pp.id=NEW.publish_plan_id
        AND pp.plan_status='PUBLISH_AUTHORIZED'
        AND pp.execution_enabled=false
        AND pp.publish_performed=false
        AND pad.decision='AUTHORIZE'
        AND pad.decision_status='CURRENT'
        AND pad.plan_sha256=pp.plan_sha256
        AND pad.dry_run_sha256=pp.dry_run_sha256
        AND rd.decision='APPROVE'
        AND rd.decision_status='CURRENT'
        AND saj.review_status='RELEASE_APPROVED'
        AND pj.job_status='QC_PASSED'
        AND pj.publish_enabled=false
        AND pj.production_execution_enabled=false
        AND pj.external_side_effects='DENY'
        AND pt.target_status='ACTIVE'
        AND pt.execution_enabled=false
        AND pt.external_publish_enabled=false
        AND NEW.platform=pp.platform
        AND NEW.target_key=pp.target_key
        AND NEW.plan_sha256=pp.plan_sha256
        AND NEW.dry_run_sha256=pp.dry_run_sha256
        AND NEW.review_decision_sha256=pp.review_decision_sha256
        AND NEW.episode_bundle_sha256=pp.episode_bundle_sha256
        AND NEW.release_review_package_sha256=pp.release_review_package_sha256
        AND NEW.target_snapshot_sha256=pp.target_snapshot_sha256
        AND NEW.publish_metadata=pp.publish_metadata
        AND NEW.authorization_decision_sha256=pad.decision_sha256
    ) INTO source_ok;

    IF NOT source_ok THEN
      RAISE EXCEPTION 'Controlled Publisher Execution requires current immutable PUBLISH_AUTHORIZED binding';
    END IF;

    IF NEW.execution_status<>'SNAPSHOT_CREATED'
       OR NEW.upload_write_count<>0
       OR NEW.publish_write_count<>0
       OR NEW.upload_outcome<>'NOT_ATTEMPTED'
       OR NEW.publish_outcome<>'NOT_ATTEMPTED'
       OR NEW.external_publish_performed THEN
      RAISE EXCEPTION 'Controlled Publisher Execution must begin as an untouched snapshot';
    END IF;
  ELSE
    IF NEW.publish_plan_id<>OLD.publish_plan_id
       OR NEW.authorization_decision_id<>OLD.authorization_decision_id
       OR NEW.provider_job_id<>OLD.provider_job_id
       OR NEW.target_id<>OLD.target_id
       OR NEW.platform<>OLD.platform
       OR NEW.target_key<>OLD.target_key
       OR NEW.account_reference<>OLD.account_reference
       OR NEW.plan_sha256<>OLD.plan_sha256
       OR NEW.dry_run_sha256<>OLD.dry_run_sha256
       OR NEW.authorization_decision_sha256<>OLD.authorization_decision_sha256
       OR NEW.review_decision_sha256<>OLD.review_decision_sha256
       OR NEW.episode_bundle_sha256<>OLD.episode_bundle_sha256
       OR NEW.release_review_package_sha256<>OLD.release_review_package_sha256
       OR NEW.render_artifact_sha256<>OLD.render_artifact_sha256
       OR NEW.target_snapshot_sha256<>OLD.target_snapshot_sha256
       OR NEW.publish_metadata<>OLD.publish_metadata
       OR NEW.execution_adapter<>OLD.execution_adapter
       OR NEW.execution_sha256<>OLD.execution_sha256
       OR NEW.upload_idempotency_key<>OLD.upload_idempotency_key
       OR NEW.publish_idempotency_key<>OLD.publish_idempotency_key
       OR NEW.created_by<>OLD.created_by
       OR NEW.created_at<>OLD.created_at THEN
      RAISE EXCEPTION 'Controlled Publisher Execution snapshot is immutable';
    END IF;

    IF OLD.upload_request_sha256 IS NOT NULL
       AND NEW.upload_request_sha256 IS DISTINCT FROM OLD.upload_request_sha256 THEN
      RAISE EXCEPTION 'Publisher upload request SHA-256 is immutable';
    END IF;
    IF OLD.provider_upload_id IS NOT NULL
       AND NEW.provider_upload_id IS DISTINCT FROM OLD.provider_upload_id THEN
      RAISE EXCEPTION 'Publisher provider upload ID is immutable';
    END IF;
    IF NEW.upload_write_count < OLD.upload_write_count
       OR NEW.upload_write_count > 1 THEN
      RAISE EXCEPTION 'Publisher upload write count is monotonic and bounded to one';
    END IF;

    IF OLD.publish_request_sha256 IS NOT NULL
       AND NEW.publish_request_sha256 IS DISTINCT FROM OLD.publish_request_sha256 THEN
      RAISE EXCEPTION 'Publisher publish request SHA-256 is immutable';
    END IF;
    IF OLD.provider_publish_id IS NOT NULL
       AND NEW.provider_publish_id IS DISTINCT FROM OLD.provider_publish_id THEN
      RAISE EXCEPTION 'Publisher provider publish ID is immutable';
    END IF;
    IF OLD.provider_publish_url IS NOT NULL
       AND NEW.provider_publish_url IS DISTINCT FROM OLD.provider_publish_url THEN
      RAISE EXCEPTION 'Publisher provider publish URL is immutable';
    END IF;
    IF NEW.publish_write_count < OLD.publish_write_count
       OR NEW.publish_write_count > 1 THEN
      RAISE EXCEPTION 'Publisher publish write count is monotonic and bounded to one';
    END IF;

    IF OLD.external_publish_performed
       AND NOT NEW.external_publish_performed THEN
      RAISE EXCEPTION 'External publish evidence is monotonic';
    END IF;

    IF NEW.execution_status<>OLD.execution_status THEN
      IF NOT (
        (OLD.execution_status='SNAPSHOT_CREATED'
          AND NEW.execution_status IN ('UPLOADING','UPLOAD_FAILED'))
        OR (OLD.execution_status='UPLOADING'
          AND NEW.execution_status IN ('UPLOAD_UNKNOWN','UPLOADED','UPLOAD_FAILED'))
        OR (OLD.execution_status='UPLOAD_UNKNOWN'
          AND NEW.execution_status IN ('UPLOADED','UPLOAD_FAILED'))
        OR (OLD.execution_status='UPLOADED'
          AND NEW.execution_status IN ('PUBLISHING','PUBLISH_FAILED'))
        OR (OLD.execution_status='PUBLISHING'
          AND NEW.execution_status IN ('PUBLISH_UNKNOWN','PUBLISHED','PUBLISH_FAILED'))
        OR (OLD.execution_status='PUBLISH_UNKNOWN'
          AND NEW.execution_status IN ('PUBLISHED','PUBLISH_FAILED'))
      ) THEN
        RAISE EXCEPTION 'Invalid Controlled Publisher Execution state transition';
      END IF;
    END IF;
  END IF;

  NEW.updated_at=now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_shrimp_publish_execution_guard
  ON shrimp_animation_publish_executions;
CREATE TRIGGER trg_shrimp_publish_execution_guard
BEFORE INSERT OR UPDATE ON shrimp_animation_publish_executions
FOR EACH ROW EXECUTE FUNCTION enforce_shrimp_publish_execution_guard();

CREATE OR REPLACE FUNCTION protect_shrimp_publish_execution_events()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'Controlled Publisher Execution events are append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_shrimp_publish_execution_events_append_only
  ON shrimp_animation_publish_execution_events;
CREATE TRIGGER trg_shrimp_publish_execution_events_append_only
BEFORE UPDATE OR DELETE ON shrimp_animation_publish_execution_events
FOR EACH ROW EXECUTE FUNCTION protect_shrimp_publish_execution_events();

CREATE OR REPLACE FUNCTION mark_shrimp_publish_execution_source_stale()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.plan_status<>'STALE' AND NEW.plan_status='STALE' THEN
    UPDATE shrimp_animation_publish_executions
    SET source_stale=true
    WHERE publish_plan_id=NEW.id
      AND source_stale=false;

    INSERT INTO shrimp_animation_publish_execution_events(
      execution_id,event_type,previous_status,next_status,actor,details)
    SELECT id,'SOURCE_STALE',execution_status,execution_status,
           'DB_STALE_TRIGGER',
           jsonb_build_object(
             'publish_plan_id',NEW.id,
             'reason','publisher_plan_became_stale'
           )
    FROM shrimp_animation_publish_executions
    WHERE publish_plan_id=NEW.id;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_shrimp_publish_execution_source_stale
  ON shrimp_animation_publish_plans;
CREATE TRIGGER trg_shrimp_publish_execution_source_stale
AFTER UPDATE ON shrimp_animation_publish_plans
FOR EACH ROW EXECUTE FUNCTION mark_shrimp_publish_execution_source_stale();

COMMENT ON COLUMN shrimp_animation_publish_executions.upload_write_count IS
  'Exactly-once upload mutation budget. A value of 1 forbids automatic replay.';
COMMENT ON COLUMN shrimp_animation_publish_executions.publish_write_count IS
  'Exactly-once publish mutation budget. A value of 1 forbids automatic replay.';
COMMENT ON COLUMN shrimp_animation_publish_executions.source_stale IS
  'Source authorization became stale after execution snapshot creation. New provider writes are forbidden; read-only reconciliation remains allowed.';
