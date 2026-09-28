ALTER TABLE openhands_executions
  ADD COLUMN IF NOT EXISTS budget_status text NOT NULL DEFAULT 'NOT_EVALUATED',
  ADD COLUMN IF NOT EXISTS gateway_request_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS prompt_tokens integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS completion_tokens integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS total_tokens integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS estimated_cost_usd numeric(12,6) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS blocked_reason text,
  ADD COLUMN IF NOT EXISTS budget_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS live_model_verified boolean NOT NULL DEFAULT false;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_openhands_budget_status'
      AND conrelid='openhands_executions'::regclass
  ) THEN
    ALTER TABLE openhands_executions
      ADD CONSTRAINT chk_openhands_budget_status
      CHECK (budget_status IN ('NOT_EVALUATED','WITHIN_BUDGET','BLOCKED'));
  END IF;
END $$;

ALTER TABLE openhands_executions
  DROP CONSTRAINT IF EXISTS chk_openhands_budget_nonnegative;

ALTER TABLE openhands_executions
  ADD CONSTRAINT chk_openhands_budget_nonnegative
  CHECK (
    gateway_request_count >= 0
    AND prompt_tokens >= 0
    AND completion_tokens >= 0
    AND total_tokens >= 0
    AND estimated_cost_usd >= 0
  );
