from app.evidence_quality import normalized_domain
from app.validation import build_ready_for, build_validation

def _row(domain: str, content: str):
    return {
        "source_url":f"https://{domain}/example",
        "source_domain":domain,
        "source_class":"community",
        "source_quality":82.0,
        "signal_strength":80.0,
        "excerpt":content[:500],
        "content":content,
    }

def test_normalized_domain_removes_www_and_port():
    assert normalized_domain("https://www.Example.com:443/a") == "example.com"

def test_cross_source_validation_can_reach_build_ready():
    rows=[
        _row(
            "source-a.example",
            "Developers need an alternative. We currently pay $20 per month. "
            "It is too expensive and the manual workflow takes hours."
        ),
        _row(
            "source-b.example",
            "Teams need a competitor replacement. Our budget is $25 per month. "
            "We are willing to pay for automation because the manual process is painful."
        ),
    ]
    result=build_validation(rows)
    dims=result["dimensions"]
    assert dims["buyer"]["status"] == "VALIDATED"
    assert dims["competitors"]["status"] == "VALIDATED"
    assert dims["pricing"]["status"] == "VALIDATED"
    assert dims["willingness_to_pay"]["status"] == "VALIDATED"
    assert dims["market_gap"]["status"] == "VALIDATED"
    assert result["completeness_score"] == 100.0
    assert build_ready_for(
        {"status":"CANDIDATE","evidence_gate_passed":True},
        result,
    )

def test_pricing_words_without_amounts_are_only_partial():
    rows=[
        _row("one.example","Developers discuss pricing and subscription plans."),
        _row("two.example","Teams compare price and subscription options."),
    ]
    result=build_validation(rows)
    assert result["dimensions"]["pricing"]["status"] == "PARTIAL"

def test_wtp_unknown_blocks_build_ready():
    rows=[
        _row(
            "one.example",
            "Developers need an alternative costing $20 per month. Manual workflow is painful."
        ),
        _row(
            "two.example",
            "Teams need a competitor replacement costing $25 per month. Manual work takes hours."
        ),
    ]
    result=build_validation(rows)
    assert result["dimensions"]["willingness_to_pay"]["status"] == "UNKNOWN"
    assert not build_ready_for(
        {"status":"CANDIDATE","evidence_gate_passed":True},
        result,
    )

def test_non_candidate_never_becomes_build_ready():
    rows=[
        _row(
            "one.example",
            "Developers need an alternative. We would pay $20 per month. Manual workflow is painful."
        ),
        _row(
            "two.example",
            "Teams need a competitor replacement. Budget is $25 per month. Manual work takes hours."
        ),
    ]
    result=build_validation(rows)
    assert not build_ready_for(
        {"status":"RESEARCH","evidence_gate_passed":True},
        result,
    )
