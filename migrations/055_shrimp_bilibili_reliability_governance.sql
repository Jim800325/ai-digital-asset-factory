-- Step 10B.14 — Reliability Governance Gate + Human Policy Decision

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_governance_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  review_key text NOT NULL UNIQUE,
  recommendation text NOT NULL,
  recommendation_reason text NOT NULL,
  review_status text NOT NULL DEFAULT 'PENDING_DECISION',
  scorecard_id uuid
    REFERENCES shrimp_bilibili_reliability_scorecards(id) ON DELETE RESTRICT,
  burn_evaluation_id uuid
    REFERENCES shrimp_bilibili_error_budget_burn_evaluations(id) ON DELETE RESTRICT,
  evidence_snapshot jsonb NOT NULL,
  evidence_sha256 char(64) NOT NULL UNIQUE,
  generated_by text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  decided_at timestamptz,
  superseded_at timestamptz,
  CHECK (recommendation IN ('NORMAL','CAUTION','FREEZE_RECOMMENDED')),
  CHECK (review_status IN ('PENDING_DECISION','DECIDED','STALE')),
  CHECK (char_length(recommendation_reason)>=3),
  CHECK (char_length(evidence_sha256)=64)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shrimp_bilibili_current_governance_review
  ON shrimp_bilibili_reliability_governance_reviews((1))
  WHERE review_status='PENDING_DECISION';

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_governance_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  review_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_reliability_governance_reviews(id) ON DELETE RESTRICT,
  decision text NOT NULL,
  reason text NOT NULL,
  actor text NOT NULL,
  evidence_sha256 char(64) NOT NULL,
  decision_sha256 char(64) NOT NULL UNIQUE,
  decided_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    decision IN (
      'ACCEPT_NORMAL',
      'ACCEPT_CAUTION',
      'AUTHORIZE_FREEZE_INTENT',
      'REJECT_RECOMMENDATION'
    )
  ),
  CHECK (char_length(reason)>=3),
  CHECK (char_length(evidence_sha256)=64),
  CHECK (char_length(decision_sha256)=64)
);

CREATE TABLE IF NOT EXISTS shrimp_bilibili_reliability_policy_intents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  review_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_reliability_governance_reviews(id) ON DELETE RESTRICT,
  decision_id uuid NOT NULL UNIQUE
    REFERENCES shrimp_bilibili_reliability_governance_decisions(id) ON DELETE RESTRICT,
  intent_type text NOT NULL,
  requested_changes jsonb NOT NULL DEFAULT '{}'::jsonb,
  intent_status text NOT NULL DEFAULT 'AUTHORIZED_NOT_EXECUTABLE',
  execution_enabled boolean NOT NULL DEFAULT false,
  changes_applied boolean NOT NULL DEFAULT false,
  intent_sha256 char(64) NOT NULL UNIQUE,
  authorized_by text NOT NULL,
  authorized_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    intent_type IN (
      'NO_CHANGE',
      'CAUTION_CONTROLS',
      'FREEZE_CHANGE_INTENT',
      'RECOMMENDATION_REJECTED'
    )
  ),
  CHECK (jsonb_typeof(requested_changes)='object'),
  CHECK (intent_status='AUTHORIZED_NOT_EXECUTABLE'),
  CHECK (execution_enabled=false),
  CHECK (changes_applied=false),
  CHECK (char_length(intent_sha256)=64)
);

CREATE OR REPLACE FUNCTION prevent_bilibili_governance_decision_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili reliability governance decision is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_governance_decision_mutation
  ON shrimp_bilibili_reliability_governance_decisions;
CREATE TRIGGER trg_prevent_bilibili_governance_decision_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_reliability_governance_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_governance_decision_mutation();

CREATE OR REPLACE FUNCTION prevent_bilibili_policy_intent_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Bilibili reliability policy intent is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_bilibili_policy_intent_mutation
  ON shrimp_bilibili_reliability_policy_intents;
CREATE TRIGGER trg_prevent_bilibili_policy_intent_mutation
BEFORE UPDATE OR DELETE ON shrimp_bilibili_reliability_policy_intents
FOR EACH ROW EXECUTE FUNCTION prevent_bilibili_policy_intent_mutation();
