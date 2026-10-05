-- Step 10B.23A — Allow compromise-recovery Root transition type.
-- Migrations 064 and 065 are already deployed and remain immutable.

ALTER TABLE shrimp_bilibili_signing_trust_roots
  DROP CONSTRAINT IF EXISTS shrimp_bilibili_signing_trust_roots_transition_check;

ALTER TABLE shrimp_bilibili_signing_trust_roots
  ADD CONSTRAINT shrimp_bilibili_signing_trust_roots_transition_check
  CHECK (
    transition_type IN (
      'BOOTSTRAP',
      'ROTATION',
      'REVOCATION',
      'POLICY_UPDATE',
      'COMPROMISE_RECOVERY'
    )
  );
