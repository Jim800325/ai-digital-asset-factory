ALTER TABLE digital_asset_opportunities
  ADD COLUMN IF NOT EXISTS research_validation_score numeric(5,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS build_readiness text NOT NULL DEFAULT 'NOT_READY';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_opportunity_build_readiness'
  ) THEN
    ALTER TABLE digital_asset_opportunities
      ADD CONSTRAINT chk_opportunity_build_readiness
      CHECK (build_readiness IN ('NOT_READY','BUILD_READY'));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS research_validations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id uuid NOT NULL UNIQUE REFERENCES digital_asset_opportunities(id) ON DELETE CASCADE,
  buyer_status text NOT NULL DEFAULT 'UNKNOWN',
  competitors_status text NOT NULL DEFAULT 'UNKNOWN',
  pricing_status text NOT NULL DEFAULT 'UNKNOWN',
  willingness_to_pay_status text NOT NULL DEFAULT 'UNKNOWN',
  market_gap_status text NOT NULL DEFAULT 'UNKNOWN',
  completeness_score numeric(5,2) NOT NULL DEFAULT 0,
  validation_gate_passed boolean NOT NULL DEFAULT false,
  build_readiness text NOT NULL DEFAULT 'NOT_READY',
  validation_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  validator_version text NOT NULL DEFAULT 'validation-v0.2-deterministic',
  observe_only boolean NOT NULL DEFAULT true,
  validated_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (buyer_status IN ('VALIDATED','PARTIAL','UNKNOWN')),
  CHECK (competitors_status IN ('VALIDATED','PARTIAL','UNKNOWN')),
  CHECK (pricing_status IN ('VALIDATED','PARTIAL','UNKNOWN')),
  CHECK (willingness_to_pay_status IN ('VALIDATED','PARTIAL','UNKNOWN')),
  CHECK (market_gap_status IN ('VALIDATED','PARTIAL','UNKNOWN')),
  CHECK (build_readiness IN ('NOT_READY','BUILD_READY'))
);

CREATE INDEX IF NOT EXISTS idx_research_validations_readiness
  ON research_validations(build_readiness,completeness_score DESC);
