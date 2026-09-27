CREATE TABLE IF NOT EXISTS sandbox_build_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  proposal_id uuid NOT NULL REFERENCES build_proposals(id) ON DELETE CASCADE,
  proposal_revision integer NOT NULL,
  source_fingerprint text NOT NULL,
  executor_kind text NOT NULL DEFAULT 'OPENHANDS',
  request_status text NOT NULL DEFAULT 'REQUESTED',
  policy_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  workspace_id uuid,
  sandbox_image text NOT NULL,
  network_policy text NOT NULL DEFAULT 'DENY',
  workspace_policy text NOT NULL DEFAULT 'ISOLATED_RW',
  external_side_effects text NOT NULL DEFAULT 'DENY',
  requested_by text NOT NULL DEFAULT 'human',
  requested_at timestamptz NOT NULL DEFAULT now(),
  policy_checked_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  error text,
  CHECK (executor_kind IN ('OPENHANDS','ACCEPTANCE')),
  CHECK (request_status IN (
    'REQUESTED','POLICY_PASSED','RUNNING','ARTIFACT_READY',
    'FAILED','BLOCKED','STALE'
  )),
  CHECK (network_policy='DENY'),
  CHECK (workspace_policy='ISOLATED_RW'),
  CHECK (external_side_effects='DENY'),
  CHECK (proposal_revision >= 1)
);

CREATE TABLE IF NOT EXISTS sandbox_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL UNIQUE REFERENCES sandbox_build_requests(id) ON DELETE CASCADE,
  workspace_id uuid NOT NULL,
  workspace_path text NOT NULL,
  executor_kind text NOT NULL,
  container_image text NOT NULL,
  container_network text NOT NULL DEFAULT 'none',
  exit_code integer,
  stdout text NOT NULL DEFAULT '',
  stderr text NOT NULL DEFAULT '',
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CHECK (executor_kind IN ('OPENHANDS','ACCEPTANCE')),
  CHECK (container_network='none')
);

CREATE TABLE IF NOT EXISTS sandbox_artifacts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES sandbox_runs(id) ON DELETE CASCADE,
  relative_path text NOT NULL,
  sha256 text NOT NULL,
  byte_size bigint NOT NULL,
  media_type text NOT NULL DEFAULT 'application/octet-stream',
  captured_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id,relative_path),
  CHECK (byte_size >= 0)
);

CREATE TABLE IF NOT EXISTS sandbox_test_results (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES sandbox_runs(id) ON DELETE CASCADE,
  test_command text NOT NULL,
  exit_code integer NOT NULL,
  stdout text NOT NULL DEFAULT '',
  stderr text NOT NULL DEFAULT '',
  passed boolean NOT NULL,
  captured_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sandbox_requests_status
  ON sandbox_build_requests(request_status,requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_sandbox_runs_request
  ON sandbox_runs(request_id);

CREATE INDEX IF NOT EXISTS idx_sandbox_artifacts_run
  ON sandbox_artifacts(run_id);

CREATE INDEX IF NOT EXISTS idx_sandbox_tests_run
  ON sandbox_test_results(run_id);
