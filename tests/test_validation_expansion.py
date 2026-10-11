from pathlib import Path

from app.validation import validation_signals
from app.validation_search_plan import useful_validation_dimensions
from app.validation_expansion_queue import validation_expansion_readiness
from app.providers.evidence_community import STACK_SITES


def test_validation_signals_reuse_validation_gate_patterns():
    signals=validation_signals(
        "Developers need an alternative because the manual workflow is painful. "
        "We currently pay $25 per month and it is too expensive."
    )
    assert "buyer_role" in signals["buyer"]
    assert "alternative" in signals["competitors"]
    assert "currency_amount" in signals["pricing"]
    assert "billing_period" in signals["pricing"]
    assert any(x in signals["willingness_to_pay"] for x in {"active_payment","paid_signal"})
    assert any(x in signals["market_gap"] for x in {"manual_pain","unmet_need"})


def test_useful_dimension_requires_new_domain_for_that_dimension():
    plan={
        "tasks":[
            {"dimension":"willingness_to_pay"},
            {"dimension":"market_gap"},
            {"dimension":"competitors"},
        ],
        "validation":{
            "dimensions":{
                "willingness_to_pay":{"domains":["github.com"]},
                "market_gap":{"domains":["github.com","news.ycombinator.com"]},
                "competitors":{"domains":[]},
            }
        },
    }
    signals={
        "willingness_to_pay":["paid_signal"],
        "market_gap":["unmet_need"],
        "competitors":["alternative"],
    }
    useful=useful_validation_dimensions(plan,"news.ycombinator.com",signals)
    assert "willingness_to_pay" in useful
    assert "competitors" in useful
    assert "market_gap" not in useful


def test_validation_expansion_defaults_fail_closed():
    readiness=validation_expansion_readiness()
    assert readiness["status"]=="BLOCKED"
    assert readiness["execution_enabled"] is False
    assert "VALIDATION_EXPANSION_DISABLED" in readiness["blockers"]
    assert "VALIDATION_EXPANSION_KEY" in readiness["blockers"]
    assert readiness["automatic_build_approval"] is False
    assert readiness["automatic_build_execution"] is False
    assert readiness["production_provider_writes"] is False
    assert readiness["automatic_publish"] is False


def test_validation_expansion_migration_and_audit_retention():
    root=Path(__file__).resolve().parents[1]
    sql=(root/"migrations"/"023_validation_expansion.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS validation_expansion_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS validation_expansion_results" in sql
    assert "ON DELETE SET NULL" in sql


def test_stack_exchange_provider_set_is_fixed():
    assert STACK_SITES == {
        "stackoverflow":"stackoverflow.com",
        "softwareengineering":"softwareengineering.stackexchange.com",
        "superuser":"superuser.com",
    }


def test_validation_plan_uses_database_retry_contract():
    root=Path(__file__).resolve().parents[1]
    source=(root/"app"/"validation_search_plan.py").read_text(encoding="utf-8")
    assert 'read_with_retry("validation_search_plan",_load)' in source
