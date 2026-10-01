CREATE TABLE IF NOT EXISTS side_business_providers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  repo_full_name text NOT NULL UNIQUE,
  github_repo_id bigint UNIQUE,
  repo_url text NOT NULL,
  name text NOT NULL,
  provider_role text NOT NULL DEFAULT 'OTHER',
  discovery_origin text NOT NULL DEFAULT 'DISCOVERED',
  description text,
  homepage text,
  language text,
  license_spdx text,
  license_policy text NOT NULL DEFAULT 'UNKNOWN',
  license_notes text,
  commercial_model text NOT NULL DEFAULT 'UNKNOWN',
  commercial_fit text NOT NULL DEFAULT 'REVIEW_REQUIRED',
  stars integer NOT NULL DEFAULT 0,
  forks integer NOT NULL DEFAULT 0,
  open_issues integer NOT NULL DEFAULT 0,
  pushed_at timestamptz,
  archived boolean NOT NULL DEFAULT false,
  license_score numeric(5,2) NOT NULL DEFAULT 0,
  activity_score numeric(5,2) NOT NULL DEFAULT 0,
  popularity_score numeric(5,2) NOT NULL DEFAULT 0,
  maintenance_score numeric(5,2) NOT NULL DEFAULT 0,
  automation_fit_score numeric(5,2) NOT NULL DEFAULT 0,
  monetization_score numeric(5,2) NOT NULL DEFAULT 0,
  score numeric(5,2) NOT NULL DEFAULT 0,
  readiness text NOT NULL DEFAULT 'RESEARCH',
  active boolean NOT NULL DEFAULT true,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_discovered_at timestamptz NOT NULL DEFAULT now(),
  last_scored_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (provider_role IN (
    'AUTOMATION','AGENT_WORKFLOW','AI_APP_PLATFORM','DATA_COLLECTION',
    'MONITORING','DISTRIBUTION','NEWSLETTER','BROWSER_AUTOMATION','OTHER'
  )),
  CHECK (discovery_origin IN ('SEED','DISCOVERED')),
  CHECK (license_policy IN (
    'PERMISSIVE','WEAK_COPYLEFT','COPYLEFT','CONDITIONAL','RESTRICTED','UNKNOWN'
  )),
  CHECK (readiness IN ('RESEARCH','WATCH','BUILD_READY','BLOCKED'))
);

CREATE TABLE IF NOT EXISTS side_business_provider_snapshots (
  id bigserial PRIMARY KEY,
  provider_id uuid NOT NULL REFERENCES side_business_providers(id) ON DELETE CASCADE,
  stars integer NOT NULL DEFAULT 0,
  forks integer NOT NULL DEFAULT 0,
  open_issues integer NOT NULL DEFAULT 0,
  pushed_at timestamptz,
  archived boolean NOT NULL DEFAULT false,
  license_spdx text,
  license_policy text NOT NULL,
  commercial_model text NOT NULL,
  license_score numeric(5,2) NOT NULL DEFAULT 0,
  activity_score numeric(5,2) NOT NULL DEFAULT 0,
  popularity_score numeric(5,2) NOT NULL DEFAULT 0,
  maintenance_score numeric(5,2) NOT NULL DEFAULT 0,
  automation_fit_score numeric(5,2) NOT NULL DEFAULT 0,
  monetization_score numeric(5,2) NOT NULL DEFAULT 0,
  score numeric(5,2) NOT NULL DEFAULT 0,
  readiness text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  captured_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS side_business_registry_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status text NOT NULL DEFAULT 'RUNNING',
  seeds_checked integer NOT NULL DEFAULT 0,
  discovered integer NOT NULL DEFAULT 0,
  refreshed integer NOT NULL DEFAULT 0,
  build_ready integer NOT NULL DEFAULT 0,
  queued integer NOT NULL DEFAULT 0,
  promoted integer NOT NULL DEFAULT 0,
  downgraded integer NOT NULL DEFAULT 0,
  errors integer NOT NULL DEFAULT 0,
  error_summary text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CHECK (status IN ('RUNNING','SUCCESS','PARTIAL','FAILED','SKIPPED'))
);

CREATE TABLE IF NOT EXISTS side_business_build_queue (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id uuid NOT NULL UNIQUE REFERENCES side_business_providers(id) ON DELETE CASCADE,
  queue_status text NOT NULL DEFAULT 'QUEUED',
  provider_score numeric(5,2) NOT NULL DEFAULT 0,
  reason text NOT NULL DEFAULT '',
  queued_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (queue_status IN ('QUEUED','STALE'))
);

CREATE INDEX IF NOT EXISTS idx_side_business_providers_score
  ON side_business_providers(readiness,score DESC,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_side_business_providers_role
  ON side_business_providers(provider_role,readiness,score DESC);
CREATE INDEX IF NOT EXISTS idx_side_business_snapshots_provider
  ON side_business_provider_snapshots(provider_id,captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_side_business_registry_runs
  ON side_business_registry_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_side_business_build_queue
  ON side_business_build_queue(queue_status,provider_score DESC,updated_at DESC);

INSERT INTO side_business_providers(
  repo_full_name,repo_url,name,provider_role,discovery_origin,
  license_policy,license_notes,commercial_model,commercial_fit,readiness
) VALUES
  ('n8n-io/n8n','https://github.com/n8n-io/n8n','n8n','AUTOMATION','SEED',
   'CONDITIONAL','Sustainable Use License; appropriate for internal business automation, review licensing before resale or hosted redistribution.',
   'workflow automation / hosted cloud / enterprise','INTERNAL_USE_RECOMMENDED','RESEARCH'),
  ('activepieces/activepieces','https://github.com/activepieces/activepieces','activepieces','AUTOMATION','SEED',
   'PERMISSIVE','Core repository is MIT with enterprise-only directories under separate terms.',
   'workflow automation / embedded automation / enterprise','COMMERCIAL_CORE_ALLOWED','RESEARCH'),
  ('langgenius/dify','https://github.com/langgenius/dify','dify','AI_APP_PLATFORM','SEED',
   'CONDITIONAL','Modified Apache terms include multi-tenant and frontend branding restrictions.',
   'AI application platform / hosted cloud / enterprise','INTERNAL_OR_SINGLE_TENANT','RESEARCH'),
  ('langflow-ai/langflow','https://github.com/langflow-ai/langflow','langflow','AGENT_WORKFLOW','SEED',
   'PERMISSIVE','MIT licensed core.',
   'AI agent workflow platform / hosted service','COMMERCIAL_ALLOWED','RESEARCH'),
  ('gitroomhq/postiz-app','https://github.com/gitroomhq/postiz-app','postiz-app','DISTRIBUTION','SEED',
   'COPYLEFT','AGPL-3.0; commercial use is possible subject to AGPL obligations.',
   'social media scheduling / hosted SaaS','AGPL_COMPLIANCE_REQUIRED','RESEARCH'),
  ('knadh/listmonk','https://github.com/knadh/listmonk','listmonk','NEWSLETTER','SEED',
   'COPYLEFT','AGPL-3.0; commercial use is possible subject to AGPL obligations.',
   'newsletter / mailing list management','AGPL_COMPLIANCE_REQUIRED','RESEARCH'),
  ('unclecode/crawl4ai','https://github.com/unclecode/crawl4ai','crawl4ai','DATA_COLLECTION','SEED',
   'PERMISSIVE','Apache-2.0.',
   'web data extraction / hosted cloud / API','COMMERCIAL_ALLOWED','RESEARCH'),
  ('apify/crawlee','https://github.com/apify/crawlee','crawlee','DATA_COLLECTION','SEED',
   'PERMISSIVE','Apache-2.0.',
   'web crawling / browser automation library','COMMERCIAL_ALLOWED','RESEARCH'),
  ('dgtlmoon/changedetection.io','https://github.com/dgtlmoon/changedetection.io','changedetection.io','MONITORING','SEED',
   'PERMISSIVE','Apache-2.0.',
   'website monitoring / hosted SaaS','COMMERCIAL_ALLOWED','RESEARCH'),
  ('browser-use/browser-use','https://github.com/browser-use/browser-use','browser-use','BROWSER_AUTOMATION','SEED',
   'PERMISSIVE','MIT.',
   'browser agent automation / hosted service','COMMERCIAL_ALLOWED','RESEARCH'),
  ('DIYgod/RSSHub','https://github.com/DIYgod/RSSHub','RSSHub','DATA_COLLECTION','SEED',
   'COPYLEFT','AGPL-3.0; commercial use is possible subject to AGPL obligations.',
   'RSS aggregation / information feeds','AGPL_COMPLIANCE_REQUIRED','RESEARCH'),
  ('bytechefhq/bytechef','https://github.com/bytechefhq/bytechef','bytechef','AUTOMATION','SEED',
   'PERMISSIVE','Core is Apache-2.0 with enterprise-only directories under separate terms.',
   'workflow automation / embedded iPaaS / enterprise','COMMERCIAL_CORE_ALLOWED','RESEARCH')
ON CONFLICT(repo_full_name) DO UPDATE SET
  provider_role=excluded.provider_role,
  discovery_origin='SEED',
  license_policy=excluded.license_policy,
  license_notes=excluded.license_notes,
  commercial_model=excluded.commercial_model,
  commercial_fit=excluded.commercial_fit,
  updated_at=now();
