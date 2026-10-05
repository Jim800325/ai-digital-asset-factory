-- Step 10B.24B — Persist Rekor verifier key with transparency receipt.
-- Migrations 067-068 remain immutable.

ALTER TABLE shrimp_bilibili_transparency_entries
  ADD COLUMN IF NOT EXISTS rekor_public_key_pem text NOT NULL DEFAULT '';
