from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.providers.animation.shrimp.bilibili_certification_trust_audit import (
    _attestation_expected_sha,
    _certification_issues,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    _sha,
)
from app.providers.animation.shrimp.bilibili_reliability_governance import (
    _recommend,
)


def _cert(*,previous=None,sequence=1,status="CERTIFIED"):
    ident=uuid4()
    snapshot={"schema_version":"test-cert","sequence":sequence}
    baseline={"schema_version":"test-baseline","score":99-sequence}
    return {
        "id":ident,
        "certification_key":f"cert-{sequence}",
        "certification_status":status,
        "certification_snapshot":snapshot,
        "stability_baseline":baseline,
        "certification_sha256":_sha(snapshot),
        "baseline_sha256":_sha(baseline),
        "previous_certification_id":previous,
        "attestation_sequence":sequence,
    }


def _att(cert,*,previous=None,sequence=1,created=None):
    row={
        "id":uuid4(),
        "certification_id":cert["id"],
        "previous_attestation_id":previous,
        "attestation_type":"INITIAL_CERTIFICATION",
        "attestation_sequence":sequence,
        "attestation_status":"VALID",
        "evidence_snapshot":{"certification_key":cert["certification_key"]},
        "created_at":created or datetime.now(timezone.utc),
    }
    evidence_sha,att_sha=_attestation_expected_sha({
        **row,
        "evidence_sha256":"",
        "attestation_sha256":"",
    })
    row["evidence_sha256"]=evidence_sha
    row["attestation_sha256"]=att_sha
    return row


def test_certification_trust_audit_ui_and_read_api():
    client=TestClient(app)
    page=client.get("/animation/reliability-review")
    assert page.status_code==200
    assert "TRUST CHAIN VERIFICATION" in page.text
    assert "RENEWAL SLA" in page.text
    assert "AUDIT PROOF EXPORT" in page.text

    response=client.get(
        "/v1/shrimp-animation/bilibili-certification-trust-audit"
    )
    assert response.status_code==200
    body=response.json()
    assert body["automatic_policy_change"] is False
    assert body["provider_writes"] is False
    assert body["secrets_redacted"] is True


def test_trust_audit_cycle_is_protected(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "ci-trust-monitor-key",
    )
    monkeypatch.setattr(settings,"cron_secret","")
    response=TestClient(app).post(
        "/internal/shrimp-animation/bilibili-certification-trust-audit-cycle",
        headers={"X-Shrimp-Health-Monitor-Key":"wrong"},
    )
    assert response.status_code==403


def test_clean_certification_and_attestation_chain_passes():
    first=_cert(sequence=1,status="SUPERSEDED")
    second=_cert(previous=first["id"],sequence=2,status="CERTIFIED")
    a1=_att(first,sequence=1)
    a2=_att(
        second,
        previous=a1["id"],
        sequence=2,
        created=datetime.now(timezone.utc),
    )
    issues,checks=_certification_issues([first,second],[a1,a2])
    assert issues==[]
    assert checks["current_certification_ids"]==[str(second["id"])]


def test_trust_audit_detects_sha_chain_and_multiple_current_failures():
    first=_cert(sequence=1,status="CERTIFIED")
    second=_cert(previous=uuid4(),sequence=2,status="CERTIFIED")
    second["baseline_sha256"]="0"*64
    a1=_att(first,sequence=1)
    a2=_att(second,previous=uuid4(),sequence=2)
    a2["attestation_sha256"]="f"*64
    issues,_=_certification_issues([first,second],[a1,a2])
    assert "BASELINE_SHA_MISMATCH" in issues
    assert "CERTIFICATION_CHAIN_POINTER_MISMATCH" in issues
    assert "ATTESTATION_CHAIN_POINTER_MISMATCH" in issues
    assert "ATTESTATION_SHA_MISMATCH" in issues
    assert "MULTIPLE_CURRENT_CERTIFICATIONS" in issues


def test_integrity_failure_requires_human_freeze_recommendation():
    snapshot={
        "scorecard":{"reliability_score":96.0,"reliability_grade":"A"},
        "burn":{"burn_status":"HEALTHY"},
        "open_regressions":[],
        "recurrence_clusters":[],
        "policy_recommendations":[],
        "post_unfreeze_observation":None,
        "post_restore_certification_reopen":None,
        "certification_lifecycle":{"certification_status":"CERTIFIED"},
        "certification_integrity_audit":{
            "audit_status":"FAIL",
            "issue_codes":["ATTESTATION_SHA_MISMATCH"],
        },
        "renewal_sla_escalations":[],
    }
    recommendation,reason=_recommend(snapshot)
    assert recommendation=="FREEZE_RECOMMENDED"
    assert "trust chain integrity audit failed" in reason


def test_renewal_sla_breach_requires_human_caution_only():
    snapshot={
        "scorecard":{"reliability_score":96.0,"reliability_grade":"A"},
        "burn":{"burn_status":"HEALTHY"},
        "open_regressions":[],
        "recurrence_clusters":[],
        "policy_recommendations":[],
        "post_unfreeze_observation":None,
        "post_restore_certification_reopen":None,
        "certification_lifecycle":{"certification_status":"EXPIRING"},
        "certification_integrity_audit":{"audit_status":"PASS","issue_codes":[]},
        "renewal_sla_escalations":[
            {
                "escalation_type":"RENEWAL_SLA_BREACH",
                "severity":"WARNING",
            }
        ],
    }
    recommendation,reason=_recommend(snapshot)
    assert recommendation=="CAUTION"
    assert "renewal SLA breached" in reason
