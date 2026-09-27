ALTER TABLE evidence ADD COLUMN IF NOT EXISTS fingerprint text;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS source_domain text;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS discovered_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_evidence_fingerprint ON evidence(fingerprint);
CREATE INDEX IF NOT EXISTS idx_evidence_domain ON evidence(source_domain);

CREATE TABLE IF NOT EXISTS opportunity_evidence (
  opportunity_id uuid NOT NULL REFERENCES digital_asset_opportunities(id) ON DELETE CASCADE,
  evidence_id uuid NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
  PRIMARY KEY(opportunity_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS discovery_sources (
  id bigserial PRIMARY KEY,
  name text NOT NULL UNIQUE,
  source_type text NOT NULL,
  base_url text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  priority int NOT NULL DEFAULT 50,
  created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO discovery_sources(name,source_type,base_url,priority) VALUES
 ('Hacker News','COMMUNITY','https://news.ycombinator.com/',80),
 ('GitHub Trending','CODE','https://github.com/trending',90),
 ('GitHub Topics AI','CODE','https://github.com/topics/artificial-intelligence',70),
 ('Product Hunt','PRODUCT','https://www.producthunt.com/',60)
ON CONFLICT(name) DO NOTHING;

CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_fingerprint
ON evidence(fingerprint) WHERE fingerprint IS NOT NULL;
