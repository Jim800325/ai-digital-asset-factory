CREATE TABLE IF NOT EXISTS sandbox_build_authorizations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  proposal_id uuid NOT NULL REFERENCES build_proposals(id) ON DELETE CASCADE,
  proposal_revision integer NOT NULL,
  approval_decision_id uuid NOT NULL REFERENCES build_proposal_decisions(id) ON DELETE RESTRICT,
  source_fingerprint text NOT NULL,
  executor_kind text NOT NULL DEFAULT 'OPENHANDS',
  authorization_status text NOT NULL,
  policy_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  policy_fingerprint text NOT NULL,
  reason text NOT NULL,
  actor text NOT NULL DEFAULT 'human-build-authorizer',
  created_at timestamptz NOT NULL DEFAULT now(),
  authorized_at timestamptz,
  rejected_at timestamptz,
  consumed_at timestamptz,
  CHECK (proposal_revision >= 1),
  CHECK (executor_kind IN ('OPENHANDS','ACCEPTANCE')),
  CHECK (authorization_status IN ('AUTHORIZED','REJECTED','CONSUMED','STALE')),
  CHECK (char_length(reason) >= 3)
);

CREATE INDEX IF NOT EXISTS idx_sandbox_build_authorizations_proposal
  ON sandbox_build_authorizations(proposal_id, proposal_revision, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sandbox_build_authorizations_status
  ON sandbox_build_authorizations(authorization_status, created_at DESC);

ALTER TABLE sandbox_build_requests
  ADD COLUMN IF NOT EXISTS authorization_id uuid
  REFERENCES sandbox_build_authorizations(id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_sandbox_build_request_authorization
  ON sandbox_build_requests(authorization_id)
  WHERE authorization_id IS NOT NULL;
