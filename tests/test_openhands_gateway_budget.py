import json

import pytest

import app.openhands_gateway as gateway

def _configure(monkeypatch,tmp_path):
    monkeypatch.setattr(gateway,"MODE","PROXY")
    monkeypatch.setattr(gateway,"AUDIT_FILE",tmp_path/"usage.json")
    monkeypatch.setattr(gateway,"ALLOWED_MODELS",["openai/allowed-model"])
    monkeypatch.setattr(gateway,"MAX_REQUESTS",2)
    monkeypatch.setattr(gateway,"MAX_PROMPT_TOKENS_PER_REQUEST",1000)
    monkeypatch.setattr(gateway,"MAX_COMPLETION_TOKENS_PER_REQUEST",100)
    monkeypatch.setattr(gateway,"MAX_TOTAL_TOKENS",1200)
    monkeypatch.setattr(gateway,"MAX_COST_PER_REQUEST_USD",0.01)
    monkeypatch.setattr(gateway,"MAX_COST_USD",0.02)
    monkeypatch.setattr(gateway,"INPUT_COST_PER_1M_USD",1.0)
    monkeypatch.setattr(gateway,"OUTPUT_COST_PER_1M_USD",2.0)

def _body(model="allowed-model",max_tokens=50,text="hello"):
    return {
        "model":model,
        "max_tokens":max_tokens,
        "messages":[{"role":"user","content":text}],
    }

def test_model_allowlist_blocks(monkeypatch,tmp_path):
    _configure(monkeypatch,tmp_path)
    state=gateway.BudgetState()
    ok,reason,_=state.preflight(_body(model="forbidden-model"))
    assert ok is False
    assert reason=="model_not_allowed"
    assert state.snapshot()["budget_status"]=="BLOCKED"

def test_completion_limit_blocks_before_upstream(monkeypatch,tmp_path):
    _configure(monkeypatch,tmp_path)
    state=gateway.BudgetState()
    ok,reason,_=state.preflight(_body(max_tokens=101))
    assert ok is False
    assert reason=="completion_token_limit_exceeded"
    assert state.request_count==0

def test_request_count_blocks(monkeypatch,tmp_path):
    _configure(monkeypatch,tmp_path)
    state=gateway.BudgetState()
    assert state.preflight(_body())[0] is True
    assert state.preflight(_body())[0] is True
    ok,reason,_=state.preflight(_body())
    assert ok is False
    assert reason=="request_limit_exceeded"
    assert state.request_count==2

def test_actual_usage_can_lock_future_requests(monkeypatch,tmp_path):
    _configure(monkeypatch,tmp_path)
    monkeypatch.setattr(gateway,"MAX_TOTAL_TOKENS",100)
    state=gateway.BudgetState()
    ok,_,reservation=state.preflight(_body(max_tokens=10))
    assert ok is True
    state.record_usage((80,30,110),reservation)
    snap=state.snapshot()
    assert snap["budget_status"]=="BLOCKED"
    assert snap["blocked_reason"]=="actual_total_token_budget_exceeded"
    assert state.preflight(_body())[0] is False

def test_audit_file_never_contains_upstream_secret(monkeypatch,tmp_path):
    _configure(monkeypatch,tmp_path)
    monkeypatch.setattr(gateway,"UPSTREAM_API_KEY","super-secret-value")
    state=gateway.BudgetState()
    state.preflight(_body())
    raw=(tmp_path/"usage.json").read_text(encoding="utf-8")
    assert "super-secret-value" not in raw
    parsed=json.loads(raw)
    assert "limits" in parsed

def test_missing_provider_usage_cannot_bypass_reserved_budget(monkeypatch,tmp_path):
    _configure(monkeypatch,tmp_path)
    monkeypatch.setattr(gateway,"MAX_TOTAL_TOKENS",120)
    state=gateway.BudgetState()
    ok,_,reservation=state.preflight(_body(max_tokens=50))
    assert ok is True
    assert reservation is not None
    first=state.total_tokens
    assert first>0
    # Simulate a streaming provider that returns no usage metadata.
    state.record_usage(None,reservation)
    ok,reason,_=state.preflight(_body(max_tokens=50))
    assert ok is False
    assert reason=="total_token_budget_exceeded"
