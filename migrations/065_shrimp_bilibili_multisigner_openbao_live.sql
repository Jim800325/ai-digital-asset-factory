-- Step 10B.23 — Multi-Signer Threshold + Dual-Control + Live Transit Acceptance + Recovery Drill

CREATE TABLE IF NOT EXISTS shrimp_bilibili_root_transition_plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  previous_root_id uuid REFERENCES shrimp_bilibili_signing_trust_roots(id) ON DELETE RESTRICT,
  candidate_root_snapshot jsonb NOT NULL,
  candidate_root_sha256 char(64) NOT NULL UNIQUE,
  previous_threshold integer NOT NULL,
  candidate_threshold integer NOT NULL,
  required_human_approvals integer NOT NULL DEFAULT 2,
  transition_type text NOT NULL,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(candidate_root_snapshot)='object'),
  CHECK (char_length(candidate_root_sha256)=64),
  CHECK (previous_threshold>=1),
  CHECK (candidate_threshold>=1),
  CHECK (required_human_approvals>=2),
  CHECK (transition_type IN ('ROTATION','COMPROMISE_RECOVERY','POLICY_UPDATE'))
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_root_transition_signatures (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL REFERENCES shrimp_bilibili_root_transition_plans(id) ON DELETE RESTRICT,
  key_fingerprint_sha256 char(64) NOT NULL,
  signature_b64 text NOT NULL,
  signer_scope text NOT NULL,
  signed_by text NOT NULL,
  signed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(plan_id,key_fingerprint_sha256),
  CHECK (char_length(key_fingerprint_sha256)=64),
  CHECK (signer_scope IN ('PREVIOUS_ROOT','CANDIDATE_ROOT','BOTH'))
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_root_transition_approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL REFERENCES shrimp_bilibili_root_transition_plans(id) ON DELETE RESTRICT,
  approver text NOT NULL,
  decision text NOT NULL,
  reason text NOT NULL,
  approval_sha256 char(64) NOT NULL UNIQUE,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(plan_id,approver),
  CHECK (decision IN ('APPROVE','REJECT')),
  CHECK (char_length(approval_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_root_transition_applications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL UNIQUE REFERENCES shrimp_bilibili_root_transition_plans(id) ON DELETE RESTRICT,
  resulting_root_id uuid REFERENCES shrimp_bilibili_signing_trust_roots(id) ON DELETE RESTRICT,
  application_status text NOT NULL,
  application_snapshot jsonb NOT NULL,
  application_sha256 char(64) NOT NULL UNIQUE,
  applied_by text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now(),
  CHECK (application_status IN ('APPLIED','REJECTED')),
  CHECK (jsonb_typeof(application_snapshot)='object'),
  CHECK (char_length(application_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_openbao_live_acceptances (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  acceptance_status text NOT NULL,
  key_name text NOT NULL,
  initial_version integer,
  rotated_version integer,
  sign_write_count integer NOT NULL DEFAULT 0,
  rotate_write_count integer NOT NULL DEFAULT 0,
  verification_passed boolean NOT NULL DEFAULT false,
  historical_verify_passed boolean NOT NULL DEFAULT false,
  acceptance_snapshot jsonb NOT NULL,
  acceptance_sha256 char(64) NOT NULL UNIQUE,
  executed_by text NOT NULL,
  executed_at timestamptz NOT NULL DEFAULT now(),
  CHECK (acceptance_status IN ('PASSED','FAILED')),
  CHECK (sign_write_count>=0),
  CHECK (rotate_write_count>=0),
  CHECK (jsonb_typeof(acceptance_snapshot)='object'),
  CHECK (char_length(acceptance_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_key_compromise_recovery_drills (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  compromised_key_fingerprint_sha256 char(64) NOT NULL,
  recovery_root_version integer,
  drill_status text NOT NULL,
  issue_codes jsonb NOT NULL,
  drill_snapshot jsonb NOT NULL,
  drill_sha256 char(64) NOT NULL UNIQUE,
  executed_by text NOT NULL,
  executed_at timestamptz NOT NULL DEFAULT now(),
  CHECK (char_length(compromised_key_fingerprint_sha256)=64),
  CHECK (drill_status IN ('PASSED','FAILED')),
  CHECK (jsonb_typeof(issue_codes)='array'),
  CHECK (jsonb_typeof(drill_snapshot)='object'),
  CHECK (char_length(drill_sha256)=64)
);

CREATE OR REPLACE FUNCTION prevent_bilibili_step10b23_mutation()
RETURNS trigger AS $immutable$
BEGIN
  RAISE EXCEPTION 'Step 10B.23 audit records are immutable';
END;
$immutable$ LANGUAGE plpgsql;

DO $triggers$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'shrimp_bilibili_root_transition_plans',
    'shrimp_bilibili_root_transition_signatures',
    'shrimp_bilibili_root_transition_approvals',
    'shrimp_bilibili_root_transition_applications',
    'shrimp_bilibili_openbao_live_acceptances',
    'shrimp_bilibili_key_compromise_recovery_drills'
  ]
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_step10b23_immutable ON %I',t);
    EXECUTE format(
      'CREATE TRIGGER trg_step10b23_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_step10b23_mutation()',
      t
    );
  END LOOP;
END
$triggers$;
