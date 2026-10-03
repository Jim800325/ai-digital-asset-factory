from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.production_provider_contract import get_provider_job
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.animation_execution import execute_animation_stage
from app.providers.animation.shrimp.execution import (
    execute_asset_stage,
    execute_voice_stage,
)
from app.providers.animation.shrimp.human_review import (
    REQUIRED_REVIEW_CHECKLIST,
    get_shrimp_review_workspace,
    list_shrimp_review_workspace,
)
from app.providers.animation.shrimp.packaging_execution import (
    execute_package_stage,
    list_episode_bundles,
    list_release_review_packages,
)
from app.providers.animation.shrimp.provider import (
    create_shrimp_animation_job,
    get_shrimp_animation_job,
    run_deterministic_planning,
    update_shrimp_content_brief,
)
from app.providers.animation.shrimp.qc_execution import execute_qc_stage
from app.providers.animation.shrimp.render_execution import execute_render_stage
from app.providers.animation.shrimp.resource_planning import run_resource_planning

from tests.test_shrimp_animation_provider_step4 import (
    FixtureRemotionAdapter,
    FixtureResolver,
    FixtureVoiceAdapter,
    NoGenerationAssetAdapter,
    _brief,
    _cleanup_registry,
    _create_proposal,
    _seed_registry,
)
from tests.test_shrimp_animation_provider_step6 import FixtureQcRenderAdapter


def _file_path(uri: str) -> Path:
    parsed = urlparse(uri)
    return Path(url2pathname(unquote(parsed.path))).resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_review_ready_job(
    proposal_id,
    reusable_media: dict,
    *,
    temp_dir: str,
    requested_by: str,
):
    brief = _brief().model_copy(update={"target_duration_ms": 10000})
    created = create_shrimp_animation_job(
        proposal_id,
        brief,
        requested_by=requested_by,
    )
    job_id = created["job_id"]

    run_deterministic_planning(job_id)
    run_resource_planning(job_id)
    execute_asset_stage(
        job_id,
        adapter=NoGenerationAssetAdapter(),
        resolver=FixtureResolver(reusable_media),
        actor=f"{requested_by}-assets",
    )
    execute_voice_stage(
        job_id,
        adapter=FixtureVoiceAdapter(),
        actor=f"{requested_by}-voices",
    )
    execute_animation_stage(
        job_id,
        adapter=FixtureRemotionAdapter(),
        actor=f"{requested_by}-animation",
    )

    render_root = Path(temp_dir) / f"{job_id}-render"
    render_root.mkdir(parents=True, exist_ok=True)
    render = execute_render_stage(
        job_id,
        adapter=FixtureQcRenderAdapter(str(render_root)),
        actor=f"{requested_by}-render",
    )
    assert render["stage_status"] == "SUCCEEDED"

    qc = execute_qc_stage(
        job_id,
        actor=f"{requested_by}-qc",
    )
    assert qc["stage_status"] == "SUCCEEDED"
    assert qc["job_status"] == "QC_PASSED"

    package_root = Path(temp_dir) / f"{job_id}-package"
    package = execute_package_stage(
        job_id,
        output_root=str(package_root),
        actor=f"{requested_by}-package",
    )
    assert package["stage_status"] == "SUCCEEDED"
    assert package["review_status"] == "READY_FOR_HUMAN_REVIEW"
    return job_id, brief, package


def _decision_payload(workspace: dict, decision: str, *, checklist=None) -> dict:
    return {
        "decision": decision,
        "reason": (
            "Human reviewer accepted the frozen animation evidence."
            if decision == "APPROVE"
            else "Human reviewer rejected the frozen animation evidence."
        ),
        "actor": "ci-shrimp-human-review",
        "episode_bundle_sha256": workspace["episode_bundle_sha256"],
        "release_review_package_sha256": workspace[
            "release_review_package_sha256"
        ],
        "confirmed_checklist": (
            list(REQUIRED_REVIEW_CHECKLIST)
            if checklist is None and decision == "APPROVE"
            else list(checklist or [])
        ),
    }


def test_step8_human_review_workspace_approve_reject_and_stale(monkeypatch):
    opportunity_ids: list[str] = []
    try:
        register_shrimp_animation_provider()
        reusable_media = _seed_registry()
        monkeypatch.setattr(
            settings,
            "shrimp_human_review_key",
            "ci-step8-human-review-key",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            # Independent APPROVE path.
            opportunity_id, proposal_id = _create_proposal()
            opportunity_ids.append(str(opportunity_id))
            approve_job, approve_brief, approve_package = _build_review_ready_job(
                proposal_id,
                reusable_media,
                temp_dir=temp_dir,
                requested_by="ci-shrimp-step8-approve",
            )

            workspace = get_shrimp_review_workspace(approve_job)
            assert workspace["review_status"] == "READY_FOR_HUMAN_REVIEW"
            assert workspace["job_status"] == "QC_PASSED"
            assert workspace["can_approve"] is True
            assert workspace["can_reject"] is True
            assert workspace["integrity_gate"]["allowed"] is True
            assert workspace["integrity_gate"]["hash_binding_ok"] is True
            assert workspace["integrity_gate"]["bundle_file_ok"] is True
            assert workspace["integrity_gate"]["review_document_ok"] is True
            assert workspace["integrity_gate"]["episode_media_ok"] is True
            assert workspace["integrity_gate"]["qc_ok"] is True
            assert workspace["integrity_gate"]["provenance_ok"] is True
            assert workspace["ui_safety"]["auto_publish"] is False
            assert workspace["ui_safety"]["production_execution_enabled"] is False
            assert set(workspace["required_checklist_keys"]) == set(
                REQUIRED_REVIEW_CHECKLIST
            )
            assert "file://" not in str(workspace)

            listed = list_shrimp_review_workspace()
            approve_list_item = next(
                item for item in listed
                if item["job_id"] == str(approve_job)
            )
            assert approve_list_item["review_status"] == (
                "READY_FOR_HUMAN_REVIEW"
            )
            assert approve_list_item["publish_enabled"] is False

            client = TestClient(app)
            page = client.get(f"/animation-review/{approve_job}")
            assert page.status_code == 200
            assert "Human Review Workspace" in page.text
            assert "自动发布永久禁用" in page.text

            detail = client.get(
                f"/v1/shrimp-animation/review-workspace/{approve_job}"
            )
            assert detail.status_code == 200
            detail_payload = detail.json()
            assert detail_payload["can_approve"] is True
            assert "file://" not in str(detail_payload)

            episode = client.get(
                f"/v1/shrimp-animation/review-workspace/"
                f"{approve_job}/episode"
            )
            assert episode.status_code == 200
            assert episode.headers["content-type"].startswith("video/mp4")
            assert len(episode.content) > 0
            assert episode.headers["x-content-sha256"] == (
                workspace["media"]["render_artifact_sha256"]
            )

            bundle_response = client.get(
                f"/v1/shrimp-animation/review-workspace/"
                f"{approve_job}/bundle"
            )
            assert bundle_response.status_code == 200
            assert bundle_response.headers["content-type"].startswith(
                "application/zip"
            )
            assert bundle_response.headers["x-content-sha256"] == (
                workspace["episode_bundle_sha256"]
            )

            review_document = client.get(
                f"/v1/shrimp-animation/review-workspace/"
                f"{approve_job}/review-document"
            )
            assert review_document.status_code == 200
            assert "Episode Review" in review_document.text

            wrong_key = client.post(
                f"/v1/shrimp-animation/review-workspace/"
                f"{approve_job}/decision",
                headers={
                    "X-Shrimp-Review-Key": "wrong-key",
                },
                json=_decision_payload(workspace, "APPROVE"),
            )
            assert wrong_key.status_code == 403

            wrong_hash_payload = _decision_payload(workspace, "APPROVE")
            wrong_hash_payload["episode_bundle_sha256"] = "0" * 64
            wrong_hash = client.post(
                f"/v1/shrimp-animation/review-workspace/"
                f"{approve_job}/decision",
                headers={
                    "X-Shrimp-Review-Key": "ci-step8-human-review-key",
                },
                json=wrong_hash_payload,
            )
            assert wrong_hash.status_code == 409

            incomplete = client.post(
                f"/v1/shrimp-animation/review-workspace/"
                f"{approve_job}/decision",
                headers={
                    "X-Shrimp-Review-Key": "ci-step8-human-review-key",
                },
                json=_decision_payload(
                    workspace,
                    "APPROVE",
                    checklist=["watch_full_episode"],
                ),
            )
            assert incomplete.status_code == 403

            approved = client.post(
                f"/v1/shrimp-animation/review-workspace/"
                f"{approve_job}/decision",
                headers={
                    "X-Shrimp-Review-Key": "ci-step8-human-review-key",
                },
                json=_decision_payload(workspace, "APPROVE"),
            )
            assert approved.status_code == 200
            approved_payload = approved.json()
            assert approved_payload["decision"] == "APPROVE"
            assert approved_payload["review_status"] == "RELEASE_APPROVED"
            assert approved_payload["publish_enabled"] is False
            assert approved_payload["production_execution_enabled"] is False
            assert approved_payload["external_publish_performed"] is False
            assert approved_payload["production_deployment_performed"] is False
            assert approved_payload["next_stage"] == "PUBLISHING_AUTHORIZATION"
            assert len(approved_payload["decision_sha256"]) == 64

            terminal = get_shrimp_review_workspace(approve_job)
            assert terminal["review_status"] == "RELEASE_APPROVED"
            assert terminal["can_approve"] is False
            assert terminal["can_reject"] is False
            assert len(terminal["decisions"]) == 1
            assert terminal["decisions"][0]["decision"] == "APPROVE"
            assert terminal["decisions"][0]["decision_status"] == "CURRENT"

            meta = get_shrimp_animation_job(approve_job)["shrimp_animation"]
            assert meta["review_status"] == "RELEASE_APPROVED"
            assert meta["reviewed_at"] is not None

            provider_job = get_provider_job(approve_job)
            assert provider_job["publish_enabled"] is False
            assert provider_job["production_execution_enabled"] is False
            assert provider_job["external_side_effects"] == "DENY"

            # PACKAGE replay must not unlock a terminal human decision.
            package_replay = execute_package_stage(
                approve_job,
                output_root=str(
                    Path(temp_dir) / f"{approve_job}-package"
                ),
                actor="ci-shrimp-step8-package-replay",
            )
            assert package_replay["replayed"] is True
            assert package_replay["review_status"] == "RELEASE_APPROVED"
            assert get_shrimp_review_workspace(approve_job)[
                "review_status"
            ] == "RELEASE_APPROVED"

            second_decision = client.post(
                f"/v1/shrimp-animation/review-workspace/"
                f"{approve_job}/decision",
                headers={
                    "X-Shrimp-Review-Key": "ci-step8-human-review-key",
                },
                json=_decision_payload(terminal, "REJECT", checklist=[]),
            )
            assert second_decision.status_code == 409

            with pytest.raises(Exception):
                with engine.begin() as db:
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_review_decisions
                          SET reason='tampered reason'
                          WHERE provider_job_id=CAST(:job_id AS uuid)
                            AND decision_status='CURRENT'
                        """),
                        {"job_id": approve_job},
                    )

            # Upstream invalidation must stale the terminal decision.
            changed_brief = approve_brief.model_copy(
                update={
                    "premise": (
                        "A changed Step 8 premise invalidates the approved "
                        "Bundle and requires a fresh human review decision."
                    )
                }
            )
            update_shrimp_content_brief(
                approve_job,
                changed_brief,
                actor="ci-shrimp-step8-invalidate",
            )
            stale = get_shrimp_animation_job(approve_job)["shrimp_animation"]
            assert stale["review_status"] == "STALE"
            assert stale["reviewed_at"] is None
            assert stale["episode_bundle_sha256"] is None
            assert stale["release_review_package_sha256"] is None
            assert list_episode_bundles(approve_job) == []
            assert list_release_review_packages(approve_job) == []

            stale_workspace = get_shrimp_review_workspace(approve_job)
            assert stale_workspace["review_status"] == "STALE"
            assert stale_workspace["can_approve"] is False
            assert stale_workspace["can_reject"] is False
            assert stale_workspace["decisions"][0][
                "decision_status"
            ] == "STALE"

            # Independent REJECT path. The shared Step 4 fixture uses
            # a fixed opportunity fingerprint, so remove the completed first
            # fixture before creating the independent second candidate.
            with engine.begin() as db:
                db.execute(
                    text("""
                      DELETE FROM digital_asset_opportunities
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": str(opportunity_id)},
                )
            opportunity_ids.remove(str(opportunity_id))

            opportunity_id2, proposal_id2 = _create_proposal()
            opportunity_ids.append(str(opportunity_id2))
            reject_job, _, _ = _build_review_ready_job(
                proposal_id2,
                reusable_media,
                temp_dir=temp_dir,
                requested_by="ci-shrimp-step8-reject",
            )
            reject_workspace = get_shrimp_review_workspace(reject_job)
            assert reject_workspace["can_reject"] is True

            # A reviewer must still be able to REJECT if the player source
            # changed after packaging; APPROVE would fail closed.
            with engine.connect() as db:
                render_uri = db.execute(
                    text("""
                      SELECT artifact_uri
                      FROM shrimp_animation_renders
                      WHERE provider_job_id=CAST(:job_id AS uuid)
                        AND render_status='CURRENT'
                    """),
                    {"job_id": reject_job},
                ).scalar_one()
            render_path = _file_path(render_uri)
            original_render = render_path.read_bytes()
            render_path.write_bytes(original_render + b"tampered-player")
            tampered_workspace = get_shrimp_review_workspace(reject_job)
            assert tampered_workspace["can_approve"] is False
            assert tampered_workspace["can_reject"] is True
            assert tampered_workspace["integrity_gate"][
                "episode_media_ok"
            ] is False
            assert "episode_player_integrity_failed" in (
                tampered_workspace["integrity_gate"]["blocking_reasons"]
            )

            reject_response = client.post(
                f"/v1/shrimp-animation/review-workspace/"
                f"{reject_job}/decision",
                headers={
                    "X-Shrimp-Review-Key": "ci-step8-human-review-key",
                },
                json=_decision_payload(
                    reject_workspace,
                    "REJECT",
                    checklist=[],
                ),
            )
            assert reject_response.status_code == 200
            rejected = reject_response.json()
            assert rejected["decision"] == "REJECT"
            assert rejected["review_status"] == "RELEASE_REJECTED"
            assert rejected["publish_enabled"] is False
            assert rejected["next_stage"] == "NONE"

            render_path.write_bytes(original_render)
            assert _sha256_file(render_path) == (
                reject_workspace["media"]["render_artifact_sha256"]
            )

            reject_terminal = get_shrimp_review_workspace(reject_job)
            assert reject_terminal["review_status"] == "RELEASE_REJECTED"
            assert reject_terminal["can_approve"] is False
            assert reject_terminal["can_reject"] is False
            assert reject_terminal["decisions"][0]["decision"] == "REJECT"

            reject_provider = get_provider_job(reject_job)
            assert reject_provider["publish_enabled"] is False
            assert reject_provider["production_execution_enabled"] is False
    finally:
        with engine.begin() as db:
            for opportunity_id in opportunity_ids:
                db.execute(
                    text("""
                      DELETE FROM digital_asset_opportunities
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": opportunity_id},
                )
        _cleanup_registry()
