ALTER TABLE digital_asset_opportunities
  ADD COLUMN IF NOT EXISTS canonical_key text,
  ADD COLUMN IF NOT EXISTS canonical_title text,
  ADD COLUMN IF NOT EXISTS independent_source_count int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS evidence_count int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS cluster_confidence numeric(5,4) NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_opportunities_canonical_key
  ON digital_asset_opportunities(canonical_key);
CREATE INDEX IF NOT EXISTS idx_opportunities_sources
  ON digital_asset_opportunities(independent_source_count DESC);

CREATE TABLE IF NOT EXISTS opportunity_aliases (
  id bigserial PRIMARY KEY,
  opportunity_id uuid NOT NULL REFERENCES digital_asset_opportunities(id) ON DELETE CASCADE,
  alias text NOT NULL,
  normalized_alias text NOT NULL,
  source_url text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(opportunity_id, normalized_alias)
);
