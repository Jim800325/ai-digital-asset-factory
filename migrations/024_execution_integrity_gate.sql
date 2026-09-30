-- Controlled Production Release Executor v0.1 — Execution Integrity Gate
-- Step 2 only. Provider writes and Production traffic changes remain disabled.

CREATE TABLE IF NOT EXISTS production_release_execution_integrity_checks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id uuid NOT NULL
    REFERENCES production_release_executions(id) ON DELETE RESTRICT,
  check_status text NOT NULL
    CHECK (check_status IN ('VERIFIED','BLOCKED')),
  actor text NOT NULL,
  execution_sha256 text NOT NULL,
  blocking_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  current_manifest_root_sha256 text,
  current_chain_head_sha256 text,
  stored_chain_head_is_ancestor boolean NOT NULL DEFAULT false,
  registry_chain_extended boolean NOT NULL DEFAULT false,
  external_side_effects text NOT NULL DEFAULT 'DENY'
    CHECK (external_side_effects='DENY'),
  production_traffic_changed boolean NOT NULL DEFAULT false
    CHECK (production_traffic_changed=false),
  checked_at timestamptz NOT NULL DEFAULT now(),
  CHECK (length(execution_sha256)=64),
  CHECK (
    current_manifest_root_sha256 IS NULL
    OR length(current_manifest_root_sha256)=64
  ),
  CHECK (
    current_chain_head_sha256 IS NULL
    OR length(current_chain_head_sha256)=64
  )
);

CREATE INDEX IF NOT EXISTS idx_production_execution_integrity_checks
  ON production_release_execution_integrity_checks(
    execution_id, checked_at DESC, id DESC
  );

DROP TRIGGER IF EXISTS trg_production_execution_integrity_append_only
  ON production_release_execution_integrity_checks;
CREATE TRIGGER trg_production_execution_integrity_append_only
BEFORE UPDATE OR DELETE ON production_release_execution_integrity_checks
FOR EACH ROW EXECUTE FUNCTION protect_production_release_audit_rows();

COMMENT ON TABLE production_release_execution_integrity_checks IS
  'Append-only Execution Integrity Gate results. Step 2 performs no provider writes.';
