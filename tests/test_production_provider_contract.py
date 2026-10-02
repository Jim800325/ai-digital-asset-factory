import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.build_proposals import decide_build_proposal
from app.db import engine
from app.main import app
from app.production_provider_contract import (
    ProviderManifestEnvelope,
    ProviderStageSpec,
    ProductionProviderSpec,
    complete_provider_stage,
    fail_provider_stage,
    get_provider_job,
    invalidate_provider_stage,
    provider_spec_payload,
    reconcile_provider_job_source,
    record_provider_resource_usage,
    register_provider_definition,
    retry_provider_stage,
    start_provider_stage,
    write_provider_manifest,
)
from app.workers.pipeline import run_pipeline


def _item(source_type: str, url: str, title: str, body: str):
    import hashlib
    return {
        "source_type": source_type,
        "url": url,
        "title": title,
        "text": body,
        "fingerprint": hashlib.sha256(
            (source_type + "|" + url).encode("utf-8")
        ).hexdigest(),
    }


def _acceptance_spec():
    return ProductionProviderSpec(
        provider_key="contract_acceptance",
        asset_class="DATASET_API",
        provider_version="v0.1-test",
        capabilities=("manifest", "retry", "cost-accounting"),
        stages=(
            ProviderStageSpec("PLAN", "PLAN", (), 2),
            ProviderStageSpec("GENERATE", "GENERATE", ("PLAN",), 2),
            ProviderStageSpec("PACKAGE", "PACKAGE", ("GENERATE",), 2),
            ProviderStageSpec("QC", "QC", ("PACKAGE",), 2),
        ),
    )


def test_provider_spec_requires_topological_stage_order():
    with pytest.raises(ValueError, match="unknown or later"):
        provider_spec_payload(
            ProductionProviderSpec(
                provider_key="bad_provider",
                asset_class="CONTENT_IP",
                provider_version="v0.1",
                stages=(
                    ProviderStageSpec("QC", "QC", ("PACKAGE",), 2),
                    ProviderStageSpec("PACKAGE", "PACKAGE", (), 2),
                ),
            )
        )


def test_production_provider_contract_end_to_end_and_fail_closed():
    urls = [
        "https://provider-contract.test/research/data-api",
        "https://provider-contract.example/pricing/data-api",
    ]
    opportunity_id = None
    provider_id = None
    try:
        result = run_pipeline(
            acceptance_items=[
                _item(
                    "NEWS_ARTICLE",
                    urls[0],
                    "Automated competitor pricing dataset API",
                    (
                        "Teams need an alternative competitor pricing dataset API. "
                        "Manual data collection is slow, difficult, and takes hours. "
                        "We pay $20 per month and need automated pricing data access."
                    ),
                ),
                _item(
                    "COMMUNITY_POST",
                    urls[1],
                    "Automated competitor pricing dataset API",
                    (
                        "Operators need a competitor pricing dataset API and tracker. "
                        "The manual workflow is expensive and slow. "
                        "Our budget is $25 per month and we are willing to pay for automation."
                    ),
                ),
            ]
        )
        assert result["opportunities"] == 1

        with engine.connect() as db:
            row = db.execute(
                text("""
                  SELECT o.id AS opportunity_id,bp.id AS proposal_id,
                         bp.proposal_status
                  FROM digital_asset_opportunities o
                  JOIN build_proposals bp ON bp.opportunity_id=o.id
                  WHERE o.source_url=:url
                """),
                {"url": urls[0]},
            ).mappings().one()
        opportunity_id = row["opportunity_id"]
        proposal_id = row["proposal_id"]
        assert row["proposal_status"] == "PENDING_APPROVAL"

        decide_build_proposal(
            proposal_id,
            decision="APPROVE",
            reason="provider contract acceptance",
            actor="ci-human",
        )

        provider = register_provider_definition(_acceptance_spec())
        provider_id = provider["provider_id"]
        assert len(provider["spec_sha256"]) == 64

        client = TestClient(app)
        denied = client.post(
            "/v1/production-provider-jobs",
            json={
                "provider_key": "contract_acceptance",
                "proposal_id": str(proposal_id),
                "requested_by": "ci-contract",
            },
        )
        assert denied.status_code == 403

        created = client.post(
            "/v1/production-provider-jobs",
            headers={"X-Approval-Key": "ci-approval-key"},
            json={
                "provider_key": "contract_acceptance",
                "proposal_id": str(proposal_id),
                "requested_by": "ci-contract",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["created"] is True
        job_id = body["job_id"]

        job = get_provider_job(job_id)
        assert job["job_status"] == "READY"
        assert job["external_side_effects"] == "DENY"
        assert job["production_execution_enabled"] is False
        assert job["publish_enabled"] is False
        assert [stage["stage_key"] for stage in job["stages"]] == [
            "PLAN", "GENERATE", "PACKAGE", "QC"
        ]

        start_provider_stage(job_id, "PLAN", actor="ci-worker")
        plan_manifest = write_provider_manifest(
            job_id,
            "PLAN",
            ProviderManifestEnvelope(
                manifest_kind="plan",
                schema_version="v1",
                payload={"dataset": "competitor-pricing", "revision": 1},
            ),
            actor="ci-worker",
        )
        assert plan_manifest["changed"] is True
        complete_provider_stage(job_id, "PLAN", actor="ci-worker")

        start_provider_stage(job_id, "GENERATE", actor="ci-worker")
        failed = fail_provider_stage(
            job_id,
            "GENERATE",
            "transient fixture failure",
            retryable=True,
            actor="ci-worker",
        )
        assert failed["job_status"] == "WAITING_RETRY"
        retry_provider_stage(job_id, "GENERATE", actor="ci-worker")
        retry = start_provider_stage(job_id, "GENERATE", actor="ci-worker")
        assert retry["attempt_count"] == 2
        write_provider_manifest(
            job_id,
            "GENERATE",
            ProviderManifestEnvelope(
                manifest_kind="dataset",
                payload={"rows": 25, "sha": "fixture"},
            ),
            actor="ci-worker",
        )
        usage = record_provider_resource_usage(
            job_id,
            stage_key="GENERATE",
            resource_type="CPU_SECONDS",
            quantity=12.5,
            unit="seconds",
            estimated_cost_usd=0.01,
            metadata={"fixture": True},
            actor="ci-worker",
        )
        assert usage["total_estimated_cost_usd"] == pytest.approx(0.01)
        complete_provider_stage(job_id, "GENERATE", actor="ci-worker")

        start_provider_stage(job_id, "PACKAGE", actor="ci-worker")
        write_provider_manifest(
            job_id,
            "PACKAGE",
            ProviderManifestEnvelope(
                manifest_kind="artifact",
                payload={"path": "artifact/data.json", "sha256": "a" * 64},
            ),
            actor="ci-worker",
        )
        package = complete_provider_stage(job_id, "PACKAGE", actor="ci-worker")
        assert package["job_status"] == "ARTIFACT_READY"

        start_provider_stage(job_id, "QC", actor="ci-worker")
        write_provider_manifest(
            job_id,
            "QC",
            ProviderManifestEnvelope(
                manifest_kind="qc_report",
                payload={"passed": True, "checks": 4},
            ),
            actor="ci-worker",
        )
        qc = complete_provider_stage(job_id, "QC", actor="ci-worker")
        assert qc["job_status"] == "QC_PASSED"

        final = get_provider_job(job_id)
        assert final["job_status"] == "QC_PASSED"
        assert final["resource_events"] == 1
        assert final["estimated_cost_usd"] == pytest.approx(0.01)
        assert final["publish_enabled"] is False

        with pytest.raises(Exception):
            with engine.begin() as db:
                db.execute(
                    text("""
                      UPDATE production_provider_manifests
                      SET content=CAST('{"tampered":true}' AS jsonb)
                      WHERE job_id=CAST(:job_id AS uuid)
                      LIMIT 1
                    """),
                    {"job_id": job_id},
                )

        invalidated = invalidate_provider_stage(
            job_id,
            "PLAN",
            reason="input brief changed",
            actor="ci-worker",
        )
        assert invalidated["job_status"] == "READY"
        assert invalidated["invalidated_stages"] == [
            "PLAN", "GENERATE", "PACKAGE", "QC"
        ]

        job = get_provider_job(job_id)
        assert all(stage["stage_status"] == "STALE" for stage in job["stages"])
        assert job["current_manifests"] == []

        with engine.begin() as db:
            db.execute(
                text("""
                  UPDATE build_proposals
                  SET proposal_status='STALE',updated_at=now()
                  WHERE id=:id
                """),
                {"id": proposal_id},
            )

        stale = reconcile_provider_job_source(job_id)
        assert stale["valid"] is False
        assert get_provider_job(job_id)["job_status"] == "STALE"
        with pytest.raises(RuntimeError):
            start_provider_stage(job_id, "PLAN")

        with pytest.raises(Exception):
            with engine.begin() as db:
                db.execute(
                    text("""
                      UPDATE production_provider_jobs
                      SET publish_enabled=true
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": job_id},
                )
    finally:
        with engine.begin() as db:
            if opportunity_id is not None:
                db.execute(
                    text("""
                      DELETE FROM digital_asset_opportunities
                      WHERE id=:id
                    """),
                    {"id": opportunity_id},
                )
            db.execute(
                text("""
                  DELETE FROM documents
                  WHERE url=ANY(:urls)
                """),
                {"urls": urls},
            )
            if provider_id is not None:
                db.execute(
                    text("""
                      DELETE FROM production_provider_definitions
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": provider_id},
                )
