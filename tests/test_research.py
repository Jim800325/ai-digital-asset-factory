from app.research import build_report_sections

def _opportunity():
    return {
        "title":"AI pricing tracker",
        "asset_type":"DATASET_API",
        "score":82.5,
        "monetization_model":"subscription / API",
        "independent_source_count":2,
        "evidence_count":2,
        "evidence_quality_score":74.0,
        "source_diversity_score":85.0,
        "signal_strength_score":72.0,
    }

def _evidence():
    return [
        {
            "source_url":"https://github.com/example/repo/issues/1",
            "source_domain":"github.com",
            "source_class":"code",
            "source_quality":78.0,
            "signal_strength":75.0,
            "excerpt":"Users request automated price monitoring.",
        },
        {
            "source_url":"https://news.ycombinator.com/item?id=1",
            "source_domain":"news.ycombinator.com",
            "source_class":"community",
            "source_quality":82.0,
            "signal_strength":69.0,
            "excerpt":"Manual competitor pricing checks take too long.",
        },
    ]

def test_report_has_required_sections():
    report=build_report_sections(_opportunity(),_evidence())
    assert set(report) == {
        "problem","buyer","existing_alternatives","evidence","monetization",
        "build_complexity","risks","why_now","evidence_snapshot"
    }

def test_report_does_not_invent_validated_alternatives():
    report=build_report_sections(_opportunity(),_evidence())
    assert "No specific existing alternative has been independently validated" in report["existing_alternatives"]

def test_report_snapshot_preserves_sources():
    report=build_report_sections(_opportunity(),_evidence())
    domains={row["source_domain"] for row in report["evidence_snapshot"]}
    assert domains == {"github.com","news.ycombinator.com"}

def test_utf8_title_is_safe():
    opportunity=_opportunity()
    opportunity["title"]="中文价格监控工具"
    report=build_report_sections(opportunity,_evidence())
    assert "中文价格监控工具" in report["problem"]
