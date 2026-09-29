-- Controlled Release Gate Test Fixture v1
-- Purpose: create a non-production TEST_ONLY release candidate whose
-- source_tree_sha256 intentionally has no matching VERIFIED Live Acceptance Audit.
-- Cleanup is performed by a later dedicated migration after manual approval testing.

INSERT INTO digital_asset_opportunities(
  id,fingerprint,title,asset_type,problem,target_customer,monetization_model,
  source_url,status,score,build_readiness,build_proposal_status)
VALUES(
  '00000000-0000-0000-0000-000000001701',
  'test-only-release-gate-fixture-v1',
  '[TEST_ONLY] Controlled Release Gate Fixture',
  'MICRO_SAAS_TOOL',
  'Verify fail-closed release approval persistence.',
  'Internal acceptance testing only.',
  'NONE',
  'https://example.invalid/test-only-release-gate-fixture',
  'WATCH',
  0,
  'NOT_READY',
  'APPROVED'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO build_proposals(
  id,opportunity_id,revision,proposal_status,title,objective,artifact_type,
  scope,success_criteria,constraints,sandbox_policy,proposed_stack,
  source_snapshot,source_fingerprint,requires_human_approval,
  execution_enabled,approved_at)
VALUES(
  '00000000-0000-0000-0000-000000001702',
  '00000000-0000-0000-0000-000000001701',
  1,
  'APPROVED',
  '[TEST_ONLY] Controlled Release Gate Fixture',
  'Exercise the Evidence Integrity Gate without deploying or releasing anything.',
  'TEST_FIXTURE',
  '{"test_only":true,"purpose":"release_gate_fail_closed"}'::jsonb,
  '["APPROVE must be blocked by integrity gate"]'::jsonb,
  '["no deployment","no git push","no external side effects"]'::jsonb,
  '{"network":"DENY","external_side_effects":"DENY"}'::jsonb,
  '["python"]'::jsonb,
  '{"test_only":true}'::jsonb,
  'test-only-release-gate-fixture-v1',
  true,
  false,
  now()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO sandbox_build_requests(
  id,proposal_id,proposal_revision,source_fingerprint,executor_kind,
  request_status,policy_snapshot,sandbox_image,network_policy,
  workspace_policy,external_side_effects,requested_by,
  policy_checked_at,started_at,finished_at)
VALUES(
  '00000000-0000-0000-0000-000000001703',
  '00000000-0000-0000-0000-000000001702',
  1,
  'test-only-release-gate-fixture-v1',
  'OPENHANDS',
  'ARTIFACT_READY',
  '{"test_only":true,"external_side_effects":"DENY"}'::jsonb,
  'test-only/fixture:1',
  'INTERNAL_GATEWAY_ONLY',
  'ISOLATED_RW',
  'DENY',
  'controlled-test-fixture',
  now(),
  now(),
  now()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO sandbox_runs(
  id,request_id,workspace_id,workspace_path,executor_kind,container_image,
  container_network,exit_code,stdout,stderr,started_at,finished_at)
VALUES(
  '00000000-0000-0000-0000-000000001704',
  '00000000-0000-0000-0000-000000001703',
  '00000000-0000-0000-0000-000000001705',
  '/tmp/test-only-release-gate-fixture',
  'OPENHANDS',
  'test-only/fixture:1',
  'internal-gateway',
  0,
  'TEST_ONLY fixture',
  '',
  now(),
  now()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO sandbox_artifacts(
  id,run_id,relative_path,sha256,byte_size,media_type)
VALUES(
  '00000000-0000-0000-0000-000000001706',
  '00000000-0000-0000-0000-000000001704',
  'fixture.txt',
  encode(digest(convert_to('TEST_ONLY fixture artifact','UTF8'),'sha256'),'hex'),
  octet_length(convert_to('TEST_ONLY fixture artifact','UTF8')),
  'text/plain'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO sandbox_artifact_contents(
  artifact_id,content_bytes,content_sha256)
VALUES(
  '00000000-0000-0000-0000-000000001706',
  convert_to('TEST_ONLY fixture artifact','UTF8'),
  encode(digest(convert_to('TEST_ONLY fixture artifact','UTF8'),'sha256'),'hex')
)
ON CONFLICT (artifact_id) DO NOTHING;

INSERT INTO sandbox_test_results(
  id,run_id,test_command,exit_code,stdout,stderr,passed)
VALUES(
  '00000000-0000-0000-0000-000000001707',
  '00000000-0000-0000-0000-000000001704',
  'controlled-fixture-self-test',
  0,
  '1 passed',
  '',
  true
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO openhands_executions(
  id,request_id,run_id,cli_version,model_name,inner_runtime,network_policy,
  gateway_mode,task_sha256,exit_code,trace_jsonl,budget_status,
  gateway_request_count,prompt_tokens,completion_tokens,total_tokens,
  estimated_cost_usd,budget_snapshot,live_model_verified,finished_at)
VALUES(
  '00000000-0000-0000-0000-000000001708',
  '00000000-0000-0000-0000-000000001703',
  '00000000-0000-0000-0000-000000001704',
  'TEST_ONLY',
  'TEST_ONLY_NO_MODEL_CALL',
  'process',
  'INTERNAL_GATEWAY_ONLY',
  'PROXY',
  repeat('a',64),
  0,
  '{"test_only":true,"note":"No model invocation performed."}',
  'WITHIN_BUDGET',
  0,
  0,
  0,
  0,
  0,
  '{"test_only":true}'::jsonb,
  true,
  now()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO release_candidates(
  id,request_id,run_id,proposal_id,proposal_revision,source_fingerprint,
  release_status,live_validation_required,artifact_manifest,
  artifact_manifest_sha256,test_summary,deployment_enabled)
VALUES(
  '00000000-0000-0000-0000-000000001709',
  '00000000-0000-0000-0000-000000001703',
  '00000000-0000-0000-0000-000000001704',
  '00000000-0000-0000-0000-000000001702',
  1,
  'test-only-release-gate-fixture-v1',
  'WAITING_LIVE_VALIDATION',
  true,
  '[{"path":"fixture.txt","test_only":true}]'::jsonb,
  repeat('b',64),
  '{"passed":1,"failed":0,"test_only":true}'::jsonb,
  false
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO release_review_packages(
  id,release_candidate_id,proposal_id,proposal_revision,run_id,
  package_status,content_snapshot_complete,artifact_manifest,artifact_diff,
  dependency_inventory,sbom,test_report,risk_summary,source_tree_sha256,
  package_sha256,generator_version)
VALUES(
  '00000000-0000-0000-0000-000000001710',
  '00000000-0000-0000-0000-000000001709',
  '00000000-0000-0000-0000-000000001702',
  1,
  '00000000-0000-0000-0000-000000001704',
  'GENERATED',
  true,
  '[{"path":"fixture.txt","test_only":true}]'::jsonb,
  '[]'::jsonb,
  '[]'::jsonb,
  '{"test_only":true}'::jsonb,
  '{"passed":1,"failed":0,"test_only":true}'::jsonb,
  '{"risk_level":"LOW","test_only":true}'::jsonb,
  'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
  'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
  'test-fixture-v1'
)
ON CONFLICT (id) DO NOTHING;

UPDATE release_candidates
SET release_status='READY_FOR_REVIEW'
WHERE id='00000000-0000-0000-0000-000000001709'
  AND release_status='WAITING_LIVE_VALIDATION';
