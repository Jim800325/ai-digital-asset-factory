from pathlib import Path

import pytest

from app.evidence_expansion import _mapping_decision
from app.evidence_expansion_queue import evidence_expansion_readiness


def test_mapping_gate_accepts_strong_same_opportunity_overlap():
    result=_mapping_decision(
        search_seed="codespaces cloud development environment",
        opportunity_title="GitHub Codespaces · Cloud Development Environment · GitHub",
        candidate_title="GitHub Codespaces alternatives for cloud development environments",
        candidate_text="Teams compare Codespaces, Coder and other cloud development environment tools.",
    )
    assert result["decision"]=="ACCEPTED"
    assert "codespaces" in result["shared_tokens"]
    assert result["score"]>=0.42


def test_mapping_gate_rejects_unrelated_candidate():
    result=_mapping_decision(
        search_seed="request history response pairs",
        opportunity_title="request history with request response pairs",
        candidate_title="How to price a design agency",
        candidate_text="Marketing and sales advice for consulting businesses.",
    )
    assert result["decision"]=="REJECTED"
    assert result["score"]<0.25


def test_evidence_expansion_defaults_fail_closed():
    readiness=evidence_expansion_readiness()
    assert readiness["status"]=="BLOCKED"
    assert readiness["execution_enabled"] is False
    assert "EVIDENCE_EXPANSION_DISABLED" in readiness["blockers"]
    assert "EVIDENCE_EXPANSION_KEY" in readiness["blockers"]
    assert readiness["production_provider_writes"] is False
    assert readiness["automatic_publish"] is False


def test_evidence_expansion_migration_exists_and_retains_audit_on_merge():
    root=Path(__file__).resolve().parents[1]
    sql=(root/"migrations"/"022_evidence_expansion.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS evidence_expansion_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS evidence_expansion_results" in sql
    assert "ON DELETE SET NULL" in sql
    assert "ON DELETE CASCADE" in sql  # results are deleted with their run


def test_expansion_provider_urls_are_fixed_public_apis():
    from app.providers.evidence_community import HN_API, STACK_API
    assert HN_API=="https://hn.algolia.com/api/v1/search"
    assert STACK_API=="https://api.stackexchange.com/2.3/search/advanced"
