from fastapi.testclient import TestClient

from app.main import app
from app.providers.animation.shrimp.control_center_final_acceptance import (
    evaluate_unified_control_center_acceptance,
)


def _passing_inputs():
    summary={
        "mode":"READ_ONLY_CONTROL_CENTER",
        "secrets_redacted":True,
        "system":{
            "vercel_env":"preview",
            "database_source":"PREVIEW_DATABASE_URL",
            "preview_isolated":True,
            "database_available":True,
            "migration_status":"CURRENT",
            "migration_expected_count":72,
            "migration_applied_count":72,
            "migration_unexpected":[],
        },
        "pipeline":{
            "total_jobs":1,
            "recent_jobs":[{
                "external_side_effects":"DENY",
                "production_execution_enabled":False,
                "publish_enabled":False,
            }],
        },
        "review":{"release_approved":1},
        "publishing":{
            "targets":[{
                "execution_enabled":False,
                "external_publish_enabled":False,
            }],
            "recent_plans":[{
                "execution_enabled":False,
                "publish_performed":False,
            }],
            "recent_executions":[{
                "upload_write_count":0,
                "publish_write_count":0,
                "external_publish_performed":False,
            }],
        },
        "bilibili":{
            "readiness":{
                "status":"BLOCKED",
                "checks":{"allowlist_denylist_disjoint":True},
                "counts":{"runnable_bilibili_executions":0},
            },
        },
    }
    trust={
        "mode":"READ_ONLY_TRUST_GOVERNANCE_CONSOLE",
        "secrets_redacted":True,
        "status":{
            "trust":"VERIFIED",
            "cloud_kms":"NOT_LIVE_ACCEPTED",
            "certification":"CERTIFIED",
            "governance":"NORMAL",
        },
        "summary":{
            "trust_chain_valid":True,
            "active_signing_keys":1,
            "trust_root_count":1,
            "hsm_key_count":1,
            "hsm_ceremony_count":1,
            "external_kms_provider_count":1,
            "certification_current":True,
            "recertification_required":False,
        },
        "safety":{
            "read_only":True,
            "cloud_kms_execution":False,
            "signing_key_rotation":False,
            "root_transition_apply":False,
            "policy_application":False,
            "production_writes":False,
            "provider_writes":False,
            "private_key_export":False,
        },
    }
    operations={
        "summary":{
            "critical_escalations":0,
            "open_circuits":0,
            "recovery_pending":0,
        },
        "secrets_redacted":True,
    }
    audit={
        "mode":"READ_ONLY_AUDIT_EVIDENCE_EXPLORER",
        "secrets_redacted":True,
        "summary":{
            "total":6,
            "with_evidence_sha256":6,
            "by_category":{
                "CERTIFICATION_AUDIT":2,
                "TRUST_ROOT":1,
                "HSM_CEREMONY":1,
                "EXTERNAL_KMS":1,
                "PUBLISHER_EXECUTION":1,
            },
        },
        "safety":{
            "read_only":True,
            "raw_secret_snapshots_exposed":False,
            "credentials_exposed":False,
            "private_keys_exposed":False,
            "production_writes":False,
            "provider_writes":False,
        },
    }
    return summary,trust,operations,audit


def test_final_acceptance_matrix_passes_without_real_cloud_acceptance():
    summary,trust,operations,audit=_passing_inputs()
    result=evaluate_unified_control_center_acceptance(
        summary=summary,
        trust=trust,
        operations=operations,
        audit=audit,
    )
    assert result["status"]=="PASSED"
    assert result["ready_for_10b27a"] is True
    assert result["summary"]["failed_checks"]==0
    assert result["next_step"].startswith("10B.27A")
    assert trust["status"]["cloud_kms"]=="NOT_LIVE_ACCEPTED"
    assert {item["id"] for item in result["deferred"]}=={
        "REAL_CLOUD_ACCOUNT_EXECUTION",
        "LIVE_CROSS_CLOUD_CEREMONY",
    }
    assert all(item["status"]=="DEFERRED" for item in result["deferred"])


def test_final_acceptance_fails_closed_on_provider_write():
    summary,trust,operations,audit=_passing_inputs()
    summary["publishing"]["recent_executions"][0]["publish_write_count"]=1
    summary["publishing"]["recent_executions"][0][
        "external_publish_performed"
    ]=True
    result=evaluate_unified_control_center_acceptance(
        summary=summary,
        trust=trust,
        operations=operations,
        audit=audit,
    )
    assert result["status"]=="FAILED"
    assert result["ready_for_10b27a"] is False
    assert "publishing.execution_zero_write" in result["summary"][
        "failed_check_ids"
    ]


def test_final_acceptance_fails_closed_on_migration_drift():
    summary,trust,operations,audit=_passing_inputs()
    summary["system"]["migration_status"]="DRIFT"
    summary["system"]["migration_unexpected"]=["999_unknown.sql"]
    result=evaluate_unified_control_center_acceptance(
        summary=summary,
        trust=trust,
        operations=operations,
        audit=audit,
    )
    assert result["status"]=="FAILED"
    assert "system.migrations_current" in result["summary"][
        "failed_check_ids"
    ]


def test_final_acceptance_fails_closed_on_missing_evidence_hash():
    summary,trust,operations,audit=_passing_inputs()
    audit["summary"]["with_evidence_sha256"]=5
    result=evaluate_unified_control_center_acceptance(
        summary=summary,
        trust=trust,
        operations=operations,
        audit=audit,
    )
    assert result["status"]=="FAILED"
    assert "evidence.hash_coverage" in result["summary"][
        "failed_check_ids"
    ]


def test_final_acceptance_endpoint_is_read_only_and_machine_readable():
    response=TestClient(app).get(
        "/v1/shrimp-animation/control-center/final-acceptance"
    )
    assert response.status_code==200
    body=response.json()
    assert body["step"]=="10B.25"
    assert body["mode"]=="READ_ONLY_FINAL_ACCEPTANCE"
    assert body["status"] in {"PASSED","FAILED"}
    assert body["safety"]=={
        "read_only":True,
        "production_writes":False,
        "provider_writes":False,
        "cloud_kms_execution":False,
        "publisher_execution":False,
        "policy_application":False,
        "private_key_export":False,
    }
    assert body["secrets_redacted"] is True
    assert body["next_step"].startswith("10B.27A")


def test_control_center_surfaces_final_acceptance_without_write_actions():
    client=TestClient(app)
    page=client.get("/")
    assert page.status_code==200
    assert 'id="acceptanceBadge"' in page.text
    assert "READ ONLY CONTROL PLANE" in page.text

    js=client.get("/review-assets/control-center.js")
    assert js.status_code==200
    assert "/v1/shrimp-animation/control-center/final-acceptance" in js.text
    assert "renderAcceptance" in js.text
    assert 'method:"POST"' not in js.text
    assert 'method:"PATCH"' not in js.text
    assert 'method:"DELETE"' not in js.text


def test_unified_control_center_main_workspaces_are_reachable():
    client=TestClient(app)
    routes=[
        "/",
        "/animation/pipeline",
        "/animation-publishing",
        "/animation-review",
        "/animation/trust-governance",
        "/animation/operations",
        "/animation/audit-evidence",
    ]
    for route in routes:
        response=client.get(route)
        assert response.status_code==200,route
        assert 'name="viewport"' in response.text,route
        assert "charset=" in response.headers["content-type"].lower(),route


def test_final_acceptance_ui_has_responsive_breakpoints():
    client=TestClient(app)
    control_css=client.get("/review-assets/control-center.css").text
    pipeline_css=client.get("/review-assets/pipeline-console.css").text
    trust_css=client.get("/review-assets/trust-governance-console.css").text
    audit_css=client.get("/review-assets/audit-evidence-explorer.css").text

    assert "@media(max-width:1180px)" in control_css
    assert "@media(max-width:760px)" in control_css
    assert "@media" in pipeline_css
    assert "@media" in trust_css
    assert "@media(max-width:1100px)" in audit_css
    assert "@media(max-width:720px)" in audit_css
