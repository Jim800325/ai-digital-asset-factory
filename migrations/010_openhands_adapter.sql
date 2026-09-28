ALTER TABLE sandbox_build_requests
  DROP CONSTRAINT IF EXISTS sandbox_build_requests_network_policy_check;

ALTER TABLE sandbox_build_requests
  ADD CONSTRAINT sandbox_build_requests_network_policy_check
  CHECK (network_policy IN ('DENY','INTERNAL_GATEWAY_ONLY'));

ALTER TABLE sandbox_runs
  DROP CONSTRAINT IF EXISTS sandbox_runs_container_network_check;

ALTER TABLE sandbox_runs
  ADD CONSTRAINT sandbox_runs_container_network_check
  CHECK (container_network IN ('none','internal-gateway'));

CREATE TABLE IF NOT EXISTS openhands_executions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL UNIQUE REFERENCES sandbox_build_requests(id) ON DELETE CASCADE,
  run_id uuid REFERENCES sandbox_runs(id) ON DELETE SET NULL,
  cli_version text NOT NULL,
  model_name text NOT NULL,
  inner_runtime text NOT NULL DEFAULT 'process',
  network_policy text NOT NULL DEFAULT 'INTERNAL_GATEWAY_ONLY',
  gateway_mode text NOT NULL,
  task_sha256 text NOT NULL,
  exit_code integer,
  trace_jsonl text NOT NULL DEFAULT '',
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CHECK (inner_runtime='process'),
  CHECK (network_policy='INTERNAL_GATEWAY_ONLY'),
  CHECK (gateway_mode IN ('MOCK','PROXY'))
);

CREATE INDEX IF NOT EXISTS idx_openhands_executions_request
  ON openhands_executions(request_id);
