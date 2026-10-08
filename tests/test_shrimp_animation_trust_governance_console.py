from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app


def test_trust_governance_console_page_is_read_only():
    client=TestClient(app)
    response=client.get("/animation/trust-governance")
    assert response.status_code == 200
    body=response.text
    assert "Trust / KMS + Governance Console" in body
    assert "READ ONLY" in body
    assert "/review-assets/trust-governance-console.js" in body
    assert "/review-assets/trust-governance-console.css" in body
    for forbidden in (
        "Rotate Key",
        "Apply Root",
        "Create Cloud Key",
        "Sign Now",
        "Apply Policy",
        "Execute Production",
    ):
        assert forbidden not in body

    homepage=client.get("/")
    assert homepage.status_code == 200
    assert "/animation/trust-governance" in homepage.text
    assert "STEP 10B.23" in homepage.text


def test_trust_governance_console_aggregates_without_write_controls(
    monkeypatch,
):
    monkeypatch.setattr(
        main_module,
        "signing_key_lifecycle_dashboard",
        lambda:{
            "active_key_count":2,
            "trust_roots":[{"root_version":3}],
            "trust_root_chain":{"verification_status":"PASS"},
            "signing_keys":[],
            "signing_provider":{"status":"READY"},
            "bundle_verification":{"verification_status":"PASS"},
        },
    )
    monkeypatch.setattr(
        main_module,
        "multisigner_dashboard",
        lambda:{
            "plans":[{"id":"plan-1","plan_status":"APPLIED"}],
            "dual_control_required":True,
            "minimum_human_approvals":2,
        },
    )
    monkeypatch.setattr(
        main_module,
        "hsm_root_custody_dashboard",
        lambda:{
            "hsm":{"status":"READY"},
            "keys":[{"id":"hsm-key"}],
            "ceremonies":[{"id":"ceremony"}],
            "restore_drills":[],
            "private_key_export_allowed":False,
        },
    )
    monkeypatch.setattr(
        main_module,
        "external_verification_dashboard",
        lambda:{
            "proof_bundles":[{"id":"proof"}],
            "export_registry_verification":{"verification_status":"PASS"},
            "external_network_writes_enabled":False,
            "automatic_policy_change":False,
        },
    )
    monkeypatch.setattr(
        main_module,
        "transparency_dashboard",
        lambda:{
            "counts":{
                "attestations":4,
                "timestamps":4,
                "transparency_entries":4,
                "offline_bundles":2,
            },
            "offline_verification":True,
            "private_key_required_for_verification":False,
        },
    )
    monkeypatch.setattr(
        main_module,
        "external_kms_dashboard",
        lambda:{
            "providers":[
                {"provider_type":"AWS_KMS"},
                {"provider_type":"GCP_KMS"},
            ],
            "failover_runs":[],
            "cross_kms_ceremonies":[],
            "external_kms_enabled":True,
            "failover_enabled":False,
            "credentials_persisted":False,
            "private_key_export_allowed":False,
        },
    )
    monkeypatch.setattr(
        main_module,
        "live_cloud_kms_dashboard",
        lambda:{
            "accepted_provider_types":["AWS_KMS","GCP_KMS"],
            "acceptances":[{"id":"run-a"},{"id":"run-b"}],
            "outage_drills":[{"id":"outage"}],
            "cross_cloud_ceremonies":[
                {"id":"cross","ceremony_status":"PASSED"}
            ],
            "live_cross_cloud_acceptance_completed":True,
            "automatic_production_writes":False,
        },
    )
    monkeypatch.setattr(
        main_module,
        "certification_dashboard",
        lambda:{
            "current_certification":{
                "id":"cert-1",
                "certification_status":"CERTIFIED",
            },
            "certifications":[],
            "baseline_immutable":True,
            "long_term_slo_promoted":True,
        },
    )
    monkeypatch.setattr(
        main_module,
        "renewal_dashboard",
        lambda:{
            "current_certification":{
                "id":"cert-1",
                "certification_status":"CERTIFIED",
            },
            "expires_in_days":21.0,
            "certification_current":True,
            "recertification_required":False,
        },
    )
    monkeypatch.setattr(
        main_module,
        "trust_audit_dashboard",
        lambda:{
            "trust_chain_valid":True,
            "integrity_audits":[{"audit_status":"PASS"}],
            "renewal_sla_hours":24,
            "missed_renewal_critical_hours":48,
        },
    )
    monkeypatch.setattr(
        main_module,
        "governance_dashboard",
        lambda:{
            "current_review":None,
            "reviews":[{"review_status":"CLOSED"}],
            "decisions":[{"decision":"APPROVE"}],
            "policy_intents":[{"id":"intent-1"}],
            "human_gate_required":True,
            "execution_supported":False,
            "changes_applied":False,
        },
    )

    client=TestClient(app)
    response=client.get("/v1/shrimp-animation/trust-governance-console")
    assert response.status_code == 200
    payload=response.json()

    assert payload["mode"] == "READ_ONLY_TRUST_GOVERNANCE_CONSOLE"
    assert payload["status"]["trust"] == "VERIFIED"
    assert payload["status"]["cloud_kms"] == "CROSS_CLOUD_ACCEPTED"
    assert payload["status"]["certification"] == "CERTIFIED"
    assert payload["status"]["governance"] == "NORMAL"
    assert payload["summary"]["active_signing_keys"] == 2
    assert payload["summary"]["hsm_key_count"] == 1
    assert payload["summary"]["external_kms_provider_count"] == 2
    assert payload["summary"]["accepted_cloud_provider_types"] == [
        "AWS_KMS","GCP_KMS"
    ]
    assert payload["summary"]["dsse_attestation_count"] == 4
    assert payload["summary"]["trust_chain_valid"] is True

    safety=payload["safety"]
    assert safety == {
        "read_only":True,
        "cloud_kms_execution":False,
        "signing_key_rotation":False,
        "root_transition_apply":False,
        "policy_application":False,
        "production_writes":False,
        "provider_writes":False,
        "private_key_export":False,
    }
    assert payload["secrets_redacted"] is True


def test_trust_governance_console_degrades_per_section(monkeypatch):
    def broken():
        raise RuntimeError("secret-bearing internal failure")

    monkeypatch.setattr(
        main_module,
        "live_cloud_kms_dashboard",
        broken,
    )
    client=TestClient(app)
    response=client.get("/v1/shrimp-animation/trust-governance-console")
    assert response.status_code == 200
    payload=response.json()
    cloud=payload["sections"]["live_cloud_kms"]
    assert cloud["available"] is False
    assert cloud["error_type"] == "RuntimeError"
    assert "secret-bearing internal failure" not in str(payload)
