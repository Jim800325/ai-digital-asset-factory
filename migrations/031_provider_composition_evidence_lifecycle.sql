ALTER TABLE side_business_composition_runs
  ADD COLUMN IF NOT EXISTS evidence_retracted integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN side_business_composition_runs.evidence_retracted IS
  'Count of previously emitted composition evidence rows removed after compositions became STALE.';
