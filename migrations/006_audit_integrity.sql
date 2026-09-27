ALTER TABLE evidence
  ADD COLUMN IF NOT EXISTS last_seen_at timestamptz NOT NULL DEFAULT now();

UPDATE evidence
SET last_seen_at=GREATEST(discovered_at,created_at)
WHERE last_seen_at IS NULL OR last_seen_at<discovered_at;

CREATE TABLE IF NOT EXISTS opportunity_fingerprints (
  fingerprint text PRIMARY KEY,
  opportunity_id uuid NOT NULL REFERENCES digital_asset_opportunities(id) ON DELETE CASCADE,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO opportunity_fingerprints(fingerprint,opportunity_id)
SELECT fingerprint,id
FROM digital_asset_opportunities
ON CONFLICT(fingerprint) DO UPDATE
SET opportunity_id=excluded.opportunity_id,
    last_seen_at=now();

CREATE INDEX IF NOT EXISTS idx_opportunity_fingerprints_opportunity
  ON opportunity_fingerprints(opportunity_id);
