ALTER TABLE evidence
  ADD COLUMN IF NOT EXISTS source_quality numeric(5,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS signal_strength numeric(5,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS source_class text NOT NULL DEFAULT 'unknown';

ALTER TABLE digital_asset_opportunities
  ADD COLUMN IF NOT EXISTS evidence_quality_score numeric(5,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS source_diversity_score numeric(5,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS signal_strength_score numeric(5,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS evidence_gate_passed boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_opportunities_evidence_gate
  ON digital_asset_opportunities(evidence_gate_passed, evidence_quality_score DESC);
