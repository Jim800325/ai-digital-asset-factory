ALTER TABLE digital_asset_opportunities
  ADD COLUMN IF NOT EXISTS build_proposal_status text NOT NULL DEFAULT 'NONE';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_opportunity_build_proposal_status'
      AND conrelid='digital_asset_opportunities'::regclass
  ) THEN
    ALTER TABLE digital_asset_opportunities
      ADD CONSTRAINT chk_opportunity_build_proposal_status
      CHECK (build_proposal_status IN ('NONE','PENDING_APPROVAL','APPROVED','REJECTED','STALE'));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS build_proposals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id uuid NOT NULL UNIQUE REFERENCES digital_asset_opportunities(id) ON DELETE CASCADE,
  revision integer NOT NULL DEFAULT 1,
  proposal_status text NOT NULL DEFAULT 'PENDING_APPROVAL',
  title text NOT NULL,
  objective text NOT NULL,
  artifact_type text NOT NULL,
  scope jsonb NOT NULL DEFAULT '{}'::jsonb,
  success_criteria jsonb NOT NULL DEFAULT '[]'::jsonb,
  constraints jsonb NOT NULL DEFAULT '[]'::jsonb,
  sandbox_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
  proposed_stack jsonb NOT NULL DEFAULT '[]'::jsonb,
  source_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  generator_version text NOT NULL DEFAULT 'build-proposal-v0.3-deterministic',
  requires_human_approval boolean NOT NULL DEFAULT true,
  execution_enabled boolean NOT NULL DEFAULT false,
  generated_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  approved_at timestamptz,
  rejected_at timestamptz,
  CHECK (proposal_status IN ('PENDING_APPROVAL','APPROVED','REJECTED','STALE')),
  CHECK (revision >= 1),
  CHECK (requires_human_approval=true),
  CHECK (execution_enabled=false)
);

CREATE TABLE IF NOT EXISTS build_proposal_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  proposal_id uuid NOT NULL REFERENCES build_proposals(id) ON DELETE CASCADE,
  proposal_revision integer NOT NULL,
  decision text NOT NULL,
  reason text NOT NULL DEFAULT '',
  actor text NOT NULL DEFAULT 'human',
  decided_at timestamptz NOT NULL DEFAULT now(),
  CHECK (decision IN ('APPROVE','REJECT')),
  CHECK (proposal_revision >= 1)
);

CREATE INDEX IF NOT EXISTS idx_build_proposals_status
  ON build_proposals(proposal_status,updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_build_proposal_decisions_proposal
  ON build_proposal_decisions(proposal_id,decided_at DESC);
