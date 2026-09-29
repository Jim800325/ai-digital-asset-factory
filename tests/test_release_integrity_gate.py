import app.release_integrity_gate as gate


def _manifest(**overrides):
    value = {
        "status": "VERIFIED",
        "manifest_present": True,
        "manifest_root_valid": True,
        "chain_valid": True,
        "manifest_root_sha256": "a" * 64,
        "chain_head_sha256": "b" * 64,
    }
    value.update(overrides)
    return value


def _audit(**overrides):
    value = {
        "audit_id": "8b385170ee00c667",
        "acceptance_status": "PASSED",
        "integrity_status": "VERIFIED",
        "source_tree_sha256": "c" * 64,
        "evidence_sha256": "d" * 64,
        "chain_sha256": "e" * 64,
        "source_commit": "1" * 40,
        "deployment_source_commit": "1" * 40,
        "vercel_deployment_id": "dpl_verified",
    }
    value.update(overrides)
    return value


def test_release_integrity_gate_allows_only_matching_verified_audit(monkeypatch):
    monkeypatch.setattr(
        gate,
        "live_acceptance_integrity_manifest",
        lambda: _manifest(),
    )
    monkeypatch.setattr(
        gate,
        "list_live_acceptance_audits",
        lambda limit=500: [_audit()],
    )

    result = gate.evaluate_release_integrity("c" * 64)

    assert result["allowed"] is True
    assert result["integrity_status"] == "VERIFIED"
    assert result["audit_id"] == "8b385170ee00c667"
    assert result["blocking_reasons"] == []
    assert result["manifest_root_valid"] is True
    assert result["chain_valid"] is True


def test_release_integrity_gate_fails_closed_when_source_tree_has_no_audit(monkeypatch):
    monkeypatch.setattr(
        gate,
        "live_acceptance_integrity_manifest",
        lambda: _manifest(),
    )
    monkeypatch.setattr(
        gate,
        "list_live_acceptance_audits",
        lambda limit=500: [_audit()],
    )

    result = gate.evaluate_release_integrity("f" * 64)

    assert result["allowed"] is False
    assert result["integrity_status"] == "ORPHANED"
    assert "matching_live_acceptance_audit_not_found" in result["blocking_reasons"]


def test_release_integrity_gate_blocks_tampered_manifest(monkeypatch):
    monkeypatch.setattr(
        gate,
        "live_acceptance_integrity_manifest",
        lambda: _manifest(
            status="TAMPERED",
            manifest_root_valid=False,
            chain_valid=False,
        ),
    )
    monkeypatch.setattr(
        gate,
        "list_live_acceptance_audits",
        lambda limit=500: [_audit()],
    )

    result = gate.evaluate_release_integrity("c" * 64)

    assert result["allowed"] is False
    assert result["integrity_status"] == "TAMPERED"
    assert "manifest_root_invalid" in result["blocking_reasons"]
    assert "integrity_chain_invalid" in result["blocking_reasons"]
    assert "integrity_manifest_status_tampered" in result["blocking_reasons"]


def test_release_integrity_gate_blocks_deployment_provenance_mismatch(monkeypatch):
    monkeypatch.setattr(
        gate,
        "live_acceptance_integrity_manifest",
        lambda: _manifest(),
    )
    monkeypatch.setattr(
        gate,
        "list_live_acceptance_audits",
        lambda limit=500: [
            _audit(deployment_source_commit="2" * 40)
        ],
    )

    result = gate.evaluate_release_integrity("c" * 64)

    assert result["allowed"] is False
    assert result["integrity_status"] == "VERIFIED"
    assert "deployment_provenance_mismatch" in result["blocking_reasons"]


def test_release_integrity_gate_blocks_duplicate_audits_for_same_tree(monkeypatch):
    monkeypatch.setattr(
        gate,
        "live_acceptance_integrity_manifest",
        lambda: _manifest(),
    )
    monkeypatch.setattr(
        gate,
        "list_live_acceptance_audits",
        lambda limit=500: [
            _audit(audit_id="aaaaaaaa11111111"),
            _audit(audit_id="bbbbbbbb22222222"),
        ],
    )

    result = gate.evaluate_release_integrity("c" * 64)

    assert result["allowed"] is False
    assert result["integrity_status"] == "TAMPERED"
    assert "multiple_live_acceptance_audits_for_source_tree" in result["blocking_reasons"]
