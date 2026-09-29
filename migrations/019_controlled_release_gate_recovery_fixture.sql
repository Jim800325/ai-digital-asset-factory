-- Controlled Release Gate Recovery Acceptance Fixture v1
-- Purpose: verify that a candidate whose artifact hash manifest matches the
-- VERIFIED Live Acceptance Audit provenance can become approvable while
-- deployment remains disabled.
-- This is TEST_ONLY and uses audit-backed hash evidence; it does not perform a model call.

INSERT INTO digital_asset_opportunities(
  id,fingerprint,title,asset_type,problem,target_customer,monetization_model,
  source_url,status,score,build_readiness,build_proposal_status)
VALUES(
  '00000000-0000-0000-0000-000000001901',
  'test-only-release-gate-recovery-v1',
  '[TEST_ONLY] Release Gate Recovery Fixture',
  'MICRO_SAAS_TOOL',
  'Verify recovery of the Release Integrity Gate with VERIFIED provenance.',
  'Internal acceptance testing only.',
  'NONE',
  'https://example.invalid/test-only-release-gate-recovery',
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
  '00000000-0000-0000-0000-000000001902',
  '00000000-0000-0000-0000-000000001901',
  1,
  'APPROVED',
  '[TEST_ONLY] Release Gate Recovery Fixture',
  'Exercise the successful provenance path without enabling deployment.',
  'TEST_FIXTURE',
  '{"test_only":true,"purpose":"release_gate_recovery"}'::jsonb,
  '["Integrity must be VERIFIED","can_approve must become true","deployment remains disabled"]'::jsonb,
  '["no deployment","no git push","no external side effects","no model call"]'::jsonb,
  '{"network":"DENY","external_side_effects":"DENY"}'::jsonb,
  '["python"]'::jsonb,
  '{"test_only":true,"provenance":"VERIFIED_LIVE_ACCEPTANCE_HASH_MANIFEST"}'::jsonb,
  'test-only-release-gate-recovery-v1',
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
  '00000000-0000-0000-0000-000000001903',
  '00000000-0000-0000-0000-000000001902',
  1,
  'test-only-release-gate-recovery-v1',
  'OPENHANDS',
  'ARTIFACT_READY',
  '{"test_only":true,"audit_backed":true,"external_side_effects":"DENY"}'::jsonb,
  'test-only/recovery-fixture:1',
  'INTERNAL_GATEWAY_ONLY',
  'ISOLATED_RW',
  'DENY',
  'controlled-recovery-fixture',
  now(),
  now(),
  now()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO sandbox_runs(
  id,request_id,workspace_id,workspace_path,executor_kind,container_image,
  container_network,exit_code,stdout,stderr,started_at,finished_at)
VALUES(
  '00000000-0000-0000-0000-000000001904',
  '00000000-0000-0000-0000-000000001903',
  '00000000-0000-0000-0000-000000001905',
  '/tmp/test-only-release-gate-recovery',
  'OPENHANDS',
  'test-only/recovery-fixture:1',
  'internal-gateway',
  0,
  'TEST_ONLY audit-backed recovery fixture',
  '',
  now(),
  now()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO sandbox_artifacts(
  id,run_id,relative_path,sha256,byte_size,media_type)
VALUES
(
  '00000000-0000-0000-0000-000000001906',
  '00000000-0000-0000-0000-000000001904',
  'README.md',
  '9da2d9dbf375d25a9d1899b3d01f40d410bfdd8150d79b4e7e4992342f7f2688',
  109,
  'text/markdown'
),
(
  '00000000-0000-0000-0000-000000001907',
  '00000000-0000-0000-0000-000000001904',
  '__pycache__/main.cpython-313.pyc',
  'e3d05d8a1d333a0be433fd3d88b75fbaee290c5228e532ca777979b12583cea5',
  465,
  'application/octet-stream'
),
(
  '00000000-0000-0000-0000-000000001908',
  '00000000-0000-0000-0000-000000001904',
  'main.py',
  '7057ef785dc9b82894bcf20c3d08d7ba3adfc030fe6d8513f6e26adf5ae1f366',
  169,
  'text/x-python'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO sandbox_test_results(
  id,run_id,test_command,exit_code,stdout,stderr,passed)
VALUES(
  '00000000-0000-0000-0000-000000001909',
  '00000000-0000-0000-0000-000000001904',
  'controlled-recovery-provenance-check',
  0,
  'VERIFIED Live Acceptance artifact hash manifest imported for TEST_ONLY recovery acceptance',
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
  '00000000-0000-0000-0000-000000001910',
  '00000000-0000-0000-0000-000000001903',
  '00000000-0000-0000-0000-000000001904',
  'TEST_ONLY',
  'TEST_ONLY_AUDIT_BACKED_RECOVERY_NO_MODEL_CALL',
  'process',
  'INTERNAL_GATEWAY_ONLY',
  'PROXY',
  repeat('c',64),
  0,
  '{"test_only":true,"audit_id":"8b385170ee00c667","note":"No model invocation performed."}',
  'WITHIN_BUDGET',
  0,
  0,
  0,
  0,
  0,
  '{"test_only":true,"audit_id":"8b385170ee00c667"}'::jsonb,
  true,
  now()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO release_candidates(
  id,request_id,run_id,proposal_id,proposal_revision,source_fingerprint,
  release_status,live_validation_required,artifact_manifest,
  artifact_manifest_sha256,test_summary,deployment_enabled)
VALUES(
  '00000000-0000-0000-0000-000000001911',
  '00000000-0000-0000-0000-000000001903',
  '00000000-0000-0000-0000-000000001904',
  '00000000-0000-0000-0000-000000001902',
  1,
  'test-only-release-gate-recovery-v1',
  'READY_FOR_REVIEW',
  true,
  '[
    {"relative_path":"README.md","sha256":"9da2d9dbf375d25a9d1899b3d01f40d410bfdd8150d79b4e7e4992342f7f2688","byte_size":109,"media_type":"text/markdown"},
    {"relative_path":"__pycache__/main.cpython-313.pyc","sha256":"e3d05d8a1d333a0be433fd3d88b75fbaee290c5228e532ca777979b12583cea5","byte_size":465,"media_type":"application/octet-stream"},
    {"relative_path":"main.py","sha256":"7057ef785dc9b82894bcf20c3d08d7ba3adfc030fe6d8513f6e26adf5ae1f366","byte_size":169,"media_type":"text/x-python"}
  ]'::jsonb,
  repeat('d',64),
  '{"passed":1,"failed":0,"test_only":true,"audit_id":"8b385170ee00c667"}'::jsonb,
  false
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO release_review_packages(
  id,release_candidate_id,proposal_id,proposal_revision,run_id,
  package_status,content_snapshot_complete,artifact_manifest,artifact_diff,
  dependency_inventory,sbom,test_report,risk_summary,source_tree_sha256,
  package_sha256,generator_version)
VALUES(
  '00000000-0000-0000-0000-000000001912',
  '00000000-0000-0000-0000-000000001911',
  '00000000-0000-0000-0000-000000001902',
  1,
  '00000000-0000-0000-0000-000000001904',
  'GENERATED',
  false,
  '[
    {"relative_path":"README.md","sha256":"9da2d9dbf375d25a9d1899b3d01f40d410bfdd8150d79b4e7e4992342f7f2688","byte_size":109,"media_type":"text/markdown"},
    {"relative_path":"__pycache__/main.cpython-313.pyc","sha256":"e3d05d8a1d333a0be433fd3d88b75fbaee290c5228e532ca777979b12583cea5","byte_size":465,"media_type":"application/octet-stream"},
    {"relative_path":"main.py","sha256":"7057ef785dc9b82894bcf20c3d08d7ba3adfc030fe6d8513f6e26adf5ae1f366","byte_size":169,"media_type":"text/x-python"}
  ]'::jsonb,
  '[]'::jsonb,
  '[]'::jsonb,
  '{"test_only":true,"provenance":"VERIFIED_LIVE_ACCEPTANCE_HASH_MANIFEST"}'::jsonb,
  '{"passed":1,"failed":0,"all_passed":true,"test_only":true}'::jsonb,
  '{
    "risk_level":"LOW",
    "test_only":true,
    "audit_backed_recovery_fixture":true,
    "audit_id":"8b385170ee00c667",
    "note":"Original artifact bytes were not persisted by Live Acceptance; this recovery fixture uses the VERIFIED audit hash manifest only."
  }'::jsonb,
  '8a6a84c1433fa6859768bfdb5ef9a57a4ec832c557bce0943e83bfaee8af46d6',
  'abababababababababababababababababababababababababababababababab',
  'test-recovery-v1'
)
ON CONFLICT (id) DO NOTHING;
