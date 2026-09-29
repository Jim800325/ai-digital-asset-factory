CREATE TABLE IF NOT EXISTS release_gate_blocks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  release_candidate_id uuid NOT NULL REFERENCES release_candidates(id) ON DELETE CASCADE,
  attempted_decision text NOT NULL CHECK (attempted_decision IN ('APPROVE')),
  actor text NOT NULL,
  reason text NOT NULL,
  integrity_status text NOT NULL CHECK (integrity_status IN ('VERIFIED','TAMPERED','ORPHANED')),
  blocking_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  live_acceptance_audit_id text,
  source_tree_sha256 text,
  audit_evidence_sha256 text,
  audit_chain_sha256 text,
  manifest_root_sha256 text,
  chain_head_sha256 text,
  vercel_deployment_id text,
  source_commit text,
  deployment_source_commit text,
  blocked_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_release_gate_blocks_candidate
  ON release_gate_blocks(release_candidate_id, blocked_at DESC);

ALTER TABLE release_gate_blocks
  DROP CONSTRAINT IF EXISTS release_gate_blocks_deployment_disabled_check;

COMMENT ON TABLE release_gate_blocks IS
  'Append-only audit trail for release approval attempts blocked by the Evidence Integrity Gate.';
