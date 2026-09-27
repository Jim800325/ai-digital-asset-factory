CREATE TABLE IF NOT EXISTS research_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id uuid NOT NULL UNIQUE REFERENCES digital_asset_opportunities(id) ON DELETE CASCADE,
  report_status text NOT NULL DEFAULT 'GENERATED',
  problem text NOT NULL,
  buyer text NOT NULL,
  existing_alternatives text NOT NULL,
  evidence text NOT NULL,
  monetization text NOT NULL,
  build_complexity text NOT NULL,
  risks text NOT NULL,
  why_now text NOT NULL,
  evidence_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
  generator_version text NOT NULL DEFAULT 'research-v0.2-deterministic',
  observe_only boolean NOT NULL DEFAULT true,
  generated_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_research_reports_generated_at
  ON research_reports(generated_at DESC);

CREATE INDEX IF NOT EXISTS idx_research_reports_opportunity
  ON research_reports(opportunity_id);
