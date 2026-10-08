from __future__ import annotations

from typing import Any


def _check(
    check_id: str,
    group: str,
    passed: bool,
    detail: str,
) -> dict[str, Any]:
    return {
        "id":check_id,
        "group":group,
        "passed":bool(passed),
        "detail":detail,
    }


def evaluate_unified_control_center_acceptance(
    *,
    summary: dict[str, Any],
    trust: dict[str, Any],
    operations: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    system=summary.get("system") or {}
    pipeline=summary.get("pipeline") or {}
    review=summary.get("review") or {}
    publishing=summary.get("publishing") or {}
    bilibili=(summary.get("bilibili") or {}).get("readiness") or {}

    trust_status=trust.get("status") or {}
    trust_summary=trust.get("summary") or {}
    trust_safety=trust.get("safety") or {}

    operations_summary=operations.get("summary") or {}
    audit_summary=audit.get("summary") or {}
    audit_safety=audit.get("safety") or {}
    audit_categories=audit_summary.get("by_category") or {}

    recent_jobs=pipeline.get("recent_jobs") or []
    targets=publishing.get("targets") or []
    plans=publishing.get("recent_plans") or []
    executions=publishing.get("recent_executions") or []

    checks=[
        _check(
            "system.read_only",
            "SYSTEM",
            summary.get("mode")=="READ_ONLY_CONTROL_CENTER"
            and summary.get("secrets_redacted") is True,
            "Control Center is read-only and secrets are redacted.",
        ),
        _check(
            "system.preview_isolated",
            "SYSTEM",
            system.get("vercel_env")=="preview"
            and system.get("database_source")=="PREVIEW_DATABASE_URL"
            and system.get("preview_isolated") is True,
            "Acceptance runs only against the isolated Preview database.",
        ),
        _check(
            "system.database_available",
            "SYSTEM",
            system.get("database_available") is True,
            "Preview database is available.",
        ),
        _check(
            "system.migrations_current",
            "SYSTEM",
            system.get("migration_status")=="CURRENT"
            and int(system.get("migration_expected_count") or 0)
                == int(system.get("migration_applied_count") or -1)
            and not (system.get("migration_unexpected") or []),
            "Active migration set is CURRENT with no unexpected versions.",
        ),
        _check(
            "pipeline.acceptance_job_present",
            "PIPELINE",
            int(pipeline.get("total_jobs") or 0)>=1,
            "At least one real Preview pipeline job is available for read-back.",
        ),
        _check(
            "pipeline.side_effects_denied",
            "PIPELINE",
            bool(recent_jobs)
            and all(
                row.get("external_side_effects")=="DENY"
                and row.get("production_execution_enabled") is False
                and row.get("publish_enabled") is False
                for row in recent_jobs
            ),
            "Pipeline jobs deny external side effects and Production execution.",
        ),
        _check(
            "review.release_approved_present",
            "PIPELINE",
            int(review.get("release_approved") or 0)>=1,
            "A Preview episode has completed Human Review and Release approval.",
        ),
        _check(
            "publishing.target_safe",
            "PUBLISHING",
            bool(targets)
            and all(
                row.get("execution_enabled") is False
                and row.get("external_publish_enabled") is False
                for row in targets
            ),
            "Publishing targets remain non-executable and external publish is disabled.",
        ),
        _check(
            "publishing.plan_safe",
            "PUBLISHING",
            bool(plans)
            and all(
                row.get("execution_enabled") is False
                and row.get("publish_performed") is False
                for row in plans
            ),
            "Authorized publishing plans remain execution-disabled and unperformed.",
        ),
        _check(
            "publishing.execution_zero_write",
            "PUBLISHING",
            bool(executions)
            and all(
                int(row.get("upload_write_count") or 0)==0
                and int(row.get("publish_write_count") or 0)==0
                and row.get("external_publish_performed") is False
                for row in executions
            ),
            "Publisher execution snapshots have zero provider-write counts.",
        ),
        _check(
            "publishing.live_execution_blocked",
            "PUBLISHING",
            bilibili.get("status")=="BLOCKED"
            and bool((bilibili.get("checks") or {}).get(
                "allowlist_denylist_disjoint"
            ))
            and int((bilibili.get("counts") or {}).get(
                "runnable_bilibili_executions"
            ) or 0)==0,
            "Live Bilibili execution remains safely blocked before real-account acceptance.",
        ),
        _check(
            "trust.read_only",
            "TRUST_GOVERNANCE",
            trust.get("mode")=="READ_ONLY_TRUST_GOVERNANCE_CONSOLE"
            and trust.get("secrets_redacted") is True,
            "Trust/Governance console is read-only and secrets are redacted.",
        ),
        _check(
            "trust.chain_verified",
            "TRUST_GOVERNANCE",
            trust_status.get("trust")=="VERIFIED"
            and trust_summary.get("trust_chain_valid") is True
            and int(trust_summary.get("active_signing_keys") or 0)>=1
            and int(trust_summary.get("trust_root_count") or 0)>=1,
            "Signing key and TUF trust root chain are verified.",
        ),
        _check(
            "trust.hsm_evidence_present",
            "TRUST_GOVERNANCE",
            int(trust_summary.get("hsm_key_count") or 0)>=1
            and int(trust_summary.get("hsm_ceremony_count") or 0)>=1,
            "HSM custody and ceremony read-back evidence are present.",
        ),
        _check(
            "trust.external_kms_evidence_present",
            "TRUST_GOVERNANCE",
            int(trust_summary.get("external_kms_provider_count") or 0)>=1,
            "External KMS provider read-back evidence is present.",
        ),
        _check(
            "trust.certification_current",
            "TRUST_GOVERNANCE",
            trust_status.get("certification") in {"CERTIFIED","CURRENT"}
            and trust_summary.get("certification_current") is True
            and trust_summary.get("recertification_required") is False,
            "Reliability certification is current and does not require recertification.",
        ),
        _check(
            "trust.governance_normal",
            "TRUST_GOVERNANCE",
            trust_status.get("governance")=="NORMAL",
            "Governance state is NORMAL.",
        ),
        _check(
            "trust.no_dangerous_actions",
            "TRUST_GOVERNANCE",
            trust_safety.get("read_only") is True
            and trust_safety.get("cloud_kms_execution") is False
            and trust_safety.get("signing_key_rotation") is False
            and trust_safety.get("root_transition_apply") is False
            and trust_safety.get("policy_application") is False
            and trust_safety.get("production_writes") is False
            and trust_safety.get("provider_writes") is False
            and trust_safety.get("private_key_export") is False,
            "Dangerous Trust/KMS/Governance actions remain disabled.",
        ),
        _check(
            "operations.clear",
            "OPERATIONS",
            int(operations_summary.get("critical_escalations") or 0)==0
            and int(operations_summary.get("open_circuits") or 0)==0
            and int(operations_summary.get("recovery_pending") or 0)==0,
            "No critical escalations, open circuits, or pending recovery.",
        ),
        _check(
            "evidence.read_only",
            "AUDIT_EVIDENCE",
            audit.get("mode")=="READ_ONLY_AUDIT_EVIDENCE_EXPLORER"
            and audit.get("secrets_redacted") is True
            and audit_safety.get("read_only") is True
            and audit_safety.get("raw_secret_snapshots_exposed") is False
            and audit_safety.get("credentials_exposed") is False
            and audit_safety.get("private_keys_exposed") is False
            and audit_safety.get("production_writes") is False
            and audit_safety.get("provider_writes") is False,
            "Evidence Explorer is read-only and exposes no secret-bearing material.",
        ),
        _check(
            "evidence.hash_coverage",
            "AUDIT_EVIDENCE",
            int(audit_summary.get("total") or 0)>=1
            and int(audit_summary.get("total") or 0)
                == int(audit_summary.get("with_evidence_sha256") or -1),
            "All current acceptance evidence rows are hash-backed.",
        ),
        _check(
            "evidence.required_categories",
            "AUDIT_EVIDENCE",
            all(
                int(audit_categories.get(name) or 0)>=1
                for name in (
                    "CERTIFICATION_AUDIT",
                    "TRUST_ROOT",
                    "HSM_CEREMONY",
                    "EXTERNAL_KMS",
                    "PUBLISHER_EXECUTION",
                )
            ),
            "Required 10B.22/10B.23 acceptance evidence is visible in the unified timeline.",
        ),
    ]

    failed=[item for item in checks if not item["passed"]]
    passed_count=len(checks)-len(failed)
    status="PASSED" if not failed else "FAILED"

    return {
        "step":"10B.25",
        "name":"Unified Control Center Final Acceptance",
        "status":status,
        "mode":"READ_ONLY_FINAL_ACCEPTANCE",
        "checks":checks,
        "summary":{
            "total_checks":len(checks),
            "passed_checks":passed_count,
            "failed_checks":len(failed),
            "failed_check_ids":[item["id"] for item in failed],
        },
        "deferred":[
            {
                "step":"10B.27A",
                "id":"REAL_CLOUD_ACCOUNT_EXECUTION",
                "status":"DEFERRED",
                "reason":"Requires real controlled AWS/GCP/Azure identities and sacrificial keys.",
            },
            {
                "step":"10B.27A",
                "id":"LIVE_CROSS_CLOUD_CEREMONY",
                "status":"DEFERRED",
                "reason":"Runs only after at least two real cloud providers are accepted.",
            },
        ],
        "ready_for_10b27a":status=="PASSED",
        "next_step":"10B.27A — Real Cloud Account Execution",
        "safety":{
            "read_only":True,
            "production_writes":False,
            "provider_writes":False,
            "cloud_kms_execution":False,
            "publisher_execution":False,
            "policy_application":False,
            "private_key_export":False,
        },
        "secrets_redacted":True,
    }
