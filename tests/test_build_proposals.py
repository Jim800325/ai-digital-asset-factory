from app.build_proposals import _proposal_payload

def _opportunity():
    return {
        "id":"00000000-0000-0000-0000-000000000001",
        "title":"Competitor pricing tracker API",
        "asset_type":"DATASET_API",
        "score":88.0,
        "build_readiness":"BUILD_READY",
        "research_validation_score":100.0,
    }

def _report():
    return {
        "id":"00000000-0000-0000-0000-000000000002",
        "report_status":"GENERATED",
        "problem":"Teams manually track competitor pricing.",
        "buyer":"Developers and pricing teams.",
        "monetization":"Subscription API.",
        "build_complexity":"MEDIUM.",
        "why_now":"Cross-source evidence is current.",
        "generator_version":"research-v0.2-deterministic",
    }

def _validation():
    return {
        "id":"00000000-0000-0000-0000-000000000003",
        "validation_status":"CURRENT",
        "validation_gate_passed":True,
        "completeness_score":100.0,
        "validator_version":"validation-v0.2-deterministic",
        "buyer_status":"VALIDATED",
        "competitors_status":"VALIDATED",
        "pricing_status":"VALIDATED",
        "willingness_to_pay_status":"VALIDATED",
        "market_gap_status":"VALIDATED",
    }

def test_proposal_payload_is_deterministic():
    one=_proposal_payload(_opportunity(),_report(),_validation())
    two=_proposal_payload(_opportunity(),_report(),_validation())
    assert one["source_fingerprint"] == two["source_fingerprint"]
    assert one["artifact_type"] == "API_SERVICE"

def test_report_basis_change_invalidates_source_fingerprint():
    before=_proposal_payload(_opportunity(),_report(),_validation())
    report=_report()
    report["problem"]="Updated evidence changes the customer problem."
    after=_proposal_payload(_opportunity(),report,_validation())
    assert before["source_fingerprint"] != after["source_fingerprint"]

def test_proposal_is_sandbox_only():
    payload=_proposal_payload(_opportunity(),_report(),_validation())
    assert payload["sandbox_policy"]["network"] == "DENY_BY_DEFAULT"
    assert payload["sandbox_policy"]["deployment"] == "DENY"
    assert payload["sandbox_policy"]["external_side_effects"] == "DENY"
    assert any("No production deployment" in item for item in payload["constraints"])
    assert any("UTF-8" in item for item in payload["success_criteria"])
