-- Archive the fixed Controlled Release Gate TEST_ONLY fixture after acceptance.
-- We archive rather than physically delete immutable review evidence.

ALTER TABLE release_candidates
  ADD COLUMN IF NOT EXISTS archived_at timestamptz;

ALTER TABLE release_gate_blocks
  ADD COLUMN IF NOT EXISTS archived_at timestamptz;

CREATE TABLE IF NOT EXISTS release_test_fixture_archives (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  release_candidate_id uuid NOT NULL,
  fixture_name text NOT NULL,
  source_fingerprint text NOT NULL,
  acceptance_status text NOT NULL,
  block_event_id uuid,
  block_reason text,
  integrity_status text,
  source_tree_sha256 text,
  manifest_root_sha256 text,
  chain_head_sha256 text,
  archived_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(release_candidate_id)
);

INSERT INTO release_test_fixture_archives(
  release_candidate_id,
  fixture_name,
  source_fingerprint,
  acceptance_status,
  block_event_id,
  block_reason,
  integrity_status,
  source_tree_sha256,
  manifest_root_sha256,
  chain_head_sha256
)
SELECT
  rc.id,
  '[TEST_ONLY] Controlled Release Gate Fixture',
  rc.source_fingerprint,
  'PASSED',
  rgb.id,
  rgb.reason,
  rgb.integrity_status,
  rgb.source_tree_sha256,
  rgb.manifest_root_sha256,
  rgb.chain_head_sha256
FROM release_candidates rc
LEFT JOIN LATERAL (
  SELECT *
  FROM release_gate_blocks
  WHERE release_candidate_id=rc.id
  ORDER BY blocked_at DESC,id DESC
  LIMIT 1
) rgb ON true
WHERE rc.id='00000000-0000-0000-0000-000000001709'
  AND rc.source_fingerprint='test-only-release-gate-fixture-v1'
ON CONFLICT (release_candidate_id) DO NOTHING;

UPDATE release_gate_blocks
SET archived_at=COALESCE(archived_at,now())
WHERE release_candidate_id='00000000-0000-0000-0000-000000001709';

UPDATE release_candidates
SET archived_at=COALESCE(archived_at,now())
WHERE id='00000000-0000-0000-0000-000000001709'
  AND source_fingerprint='test-only-release-gate-fixture-v1';
