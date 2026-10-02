from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import pytest
from sqlalchemy import text

from app.db import engine
from app.production_provider_contract import get_provider_job
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.animation_execution import execute_animation_stage
from app.providers.animation.shrimp.execution import (
    execute_asset_stage,
    execute_voice_stage,
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


def test_step7_episode_bundle_and_human_review_package():
    opportunity_id = None
    try:
        register_shrimp_animation_provider()
        reusable_media = _seed_registry()
        opportunity_id, proposal_id = _create_proposal()

        brief = _brief().model_copy(
            update={"target_duration_ms": 12000}
        )
        created = create_shrimp_animation_job(
            proposal_id,
            brief,
            requested_by="ci-shrimp-step7",
        )
        job_id = created["job_id"]

        run_deterministic_planning(job_id)
        run_resource_planning(job_id)
        execute_asset_stage(
            job_id,
            adapter=NoGenerationAssetAdapter(),
            resolver=FixtureResolver(reusable_media),
            actor="ci-assets-step7",
        )
        execute_voice_stage(
            job_id,
            adapter=FixtureVoiceAdapter(),
            actor="ci-voices-step7",
        )
        execute_animation_stage(
            job_id,
            adapter=FixtureRemotionAdapter(),
            actor="ci-animation-step7",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            renderer = FixtureQcRenderAdapter(temp_dir)
            rendered = execute_render_stage(
                job_id,
                adapter=renderer,
                actor="ci-render-step7",
            )
            assert rendered["stage_status"] == "SUCCEEDED"
            assert rendered["frame_count"] == 360

            qc = execute_qc_stage(
                job_id,
                actor="ci-qc-step7",
            )
            assert qc["stage_status"] == "SUCCEEDED"
            assert qc["job_status"] == "QC_PASSED"
            assert qc["next_stage"] == "PACKAGE"

            before_package = get_provider_job(job_id)
            before_status = {
                row["stage_key"]: row["stage_status"]
                for row in before_package["stages"]
            }
            assert before_status["QC"] == "SUCCEEDED"
            assert before_status["PACKAGE"] == "PENDING"
            assert before_package["job_status"] == "QC_PASSED"
            assert before_package["completed_at"] is None

            package_root = Path(temp_dir) / "package-output"
            result = execute_package_stage(
                job_id,
                output_root=str(package_root),
                actor="ci-package-step7",
            )
            assert result["stage_status"] == "SUCCEEDED"
            assert result["job_status"] == "QC_PASSED"
            assert result["review_status"] == "READY_FOR_HUMAN_REVIEW"
            assert result["bundle_artifact_count"] == 7
            assert result["next_stage"] == "HUMAN_REVIEW"
            assert result["publish_enabled"] is False
            assert result["production_execution_enabled"] is False
            assert len(result["episode_bundle_sha256"]) == 64
            assert len(result["release_review_package_sha256"]) == 64

            job = get_provider_job(job_id)
            statuses = {
                row["stage_key"]: row["stage_status"]
                for row in job["stages"]
            }
            assert statuses["QC"] == "SUCCEEDED"
            assert statuses["PACKAGE"] == "SUCCEEDED"
            assert job["job_status"] == "QC_PASSED"
            assert job["completed_at"] is not None
            assert job["publish_enabled"] is False
            assert job["production_execution_enabled"] is False

            meta = get_shrimp_animation_job(job_id)["shrimp_animation"]
            assert meta["review_status"] == "READY_FOR_HUMAN_REVIEW"
            assert (
                meta["episode_bundle_sha256"]
                == result["episode_bundle_sha256"]
            )
            assert (
                meta["release_review_package_sha256"]
                == result["release_review_package_sha256"]
            )

            bundles = list_episode_bundles(job_id)
            reviews = list_release_review_packages(job_id)
            assert len(bundles) == 1
            assert len(reviews) == 1
            bundle = bundles[0]
            review = reviews[0]

            bundle_path = _file_path(bundle["bundle_uri"])
            review_path = _file_path(review["review_document_uri"])
            assert bundle_path.is_file()
            assert review_path.is_file()
            assert _sha256_file(bundle_path) == bundle["bundle_sha256"]
            assert _sha256_file(review_path) == review[
                "review_document_sha256"
            ]

            expected_names = {
                "episode.mp4",
                "timeline.json",
                "subtitles.srt",
                "asset_provenance.json",
                "voice_provenance.json",
                "qc_report.json",
                "manifest_hashes.json",
                "bundle_manifest.json",
            }
            with zipfile.ZipFile(bundle_path, "r") as archive:
                assert set(archive.namelist()) == expected_names
                for info in archive.infolist():
                    assert info.date_time == (1980, 1, 1, 0, 0, 0)
                    assert info.compress_type == zipfile.ZIP_STORED

                bundle_manifest = json.loads(
                    archive.read("bundle_manifest.json")
                )
                assert (
                    bundle_manifest["packaging_version"]
                    == "episode-package-v0.1-deterministic"
                )
                assert len(bundle_manifest["artifacts"]) == 7
                manifest_names = {
                    item["relative_path"]
                    for item in bundle_manifest["artifacts"]
                }
                assert manifest_names == expected_names - {
                    "bundle_manifest.json"
                }

                subtitle_text = archive.read(
                    "subtitles.srt"
                ).decode("utf-8")
                assert " --> " in subtitle_text
                assert brief.characters[0].display_name not in subtitle_text
                assert any(
                    character.character_id in subtitle_text
                    for character in brief.characters
                )

                hashes = json.loads(
                    archive.read("manifest_hashes.json")
                )
                stages = {
                    row["stage_key"]
                    for row in hashes["manifests"]
                }
                assert {
                    "CONTENT_BRIEF",
                    "STORY",
                    "SCRIPT",
                    "SCENE",
                    "ASSETS",
                    "VOICES",
                    "ANIMATION",
                    "RENDER",
                    "QC",
                }.issubset(stages)

                asset_provenance = json.loads(
                    archive.read("asset_provenance.json")
                )
                voice_provenance = json.loads(
                    archive.read("voice_provenance.json")
                )
                assert asset_provenance["provenance"][
                    "all_usage_rights_approved"
                ] is True
                assert voice_provenance["provenance"][
                    "all_usage_rights_approved"
                ] is True

            review_content = review["package_content"]
            assert review_content["review_status"] == "READY_FOR_HUMAN_REVIEW"
            assert review_content["qc"]["passed"] is True
            assert review_content["qc"]["hard_failure_count"] == 0
            assert review_content["safety"]["human_review_required"] is True
            assert review_content["safety"]["auto_publish"] is False
            assert review_content["safety"][
                "production_execution_enabled"
            ] is False
            assert review_content["provenance"]["assets"][
                "all_usage_rights_approved"
            ] is True
            assert review_content["provenance"]["voices"][
                "all_usage_rights_approved"
            ] is True
            assert len(review_content["transcript"]) > 0
            assert len(review_content["reviewer_checklist"]) == 5
            assert all(
                item["required"]
                for item in review_content["reviewer_checklist"]
            )
            assert review_content["bundle"]["sha256"] == bundle[
                "bundle_sha256"
            ]
            assert "uri" not in review_content["bundle"]

            markdown = review_path.read_text(encoding="utf-8")
            assert "# Episode Review" in markdown
            assert "## QC" in markdown
            assert "## Provenance" in markdown
            assert "## Transcript" in markdown
            assert "## Human Review Checklist" in markdown
            assert "Automatic publish: false" in markdown

            replay = execute_package_stage(
                job_id,
                output_root=str(package_root),
                actor="ci-package-step7",
            )
            assert replay["replayed"] is True
            assert (
                replay["episode_bundle_sha256"]
                == result["episode_bundle_sha256"]
            )
            assert (
                replay["release_review_package_sha256"]
                == result["release_review_package_sha256"]
            )
            assert len(list_episode_bundles(job_id)) == 1
            assert len(list_release_review_packages(job_id)) == 1

            original_bundle_bytes = bundle_path.read_bytes()
            bundle_path.write_bytes(original_bundle_bytes + b"tamper")
            with pytest.raises(
                RuntimeError,
                match="episode bundle bytes drift",
            ):
                execute_package_stage(
                    job_id,
                    output_root=str(package_root),
                    actor="ci-package-step7-tamper",
                )
            bundle_path.write_bytes(original_bundle_bytes)
            assert _sha256_file(bundle_path) == bundle["bundle_sha256"]

            original_review_bytes = review_path.read_bytes()
            review_path.write_bytes(original_review_bytes + b"\ntamper")
            with pytest.raises(
                RuntimeError,
                match="review document bytes drift",
            ):
                execute_package_stage(
                    job_id,
                    output_root=str(package_root),
                    actor="ci-package-step7-review-tamper",
                )
            review_path.write_bytes(original_review_bytes)
            assert (
                _sha256_file(review_path)
                == review["review_document_sha256"]
            )

            with pytest.raises(Exception):
                with engine.begin() as db:
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_episode_bundles
                          SET bundle_sha256=repeat('0',64)
                          WHERE provider_job_id=CAST(:job_id AS uuid)
                            AND bundle_status='CURRENT'
                        """),
                        {"job_id": job_id},
                    )

            with pytest.raises(Exception):
                with engine.begin() as db:
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_release_review_packages
                          SET package_sha256=repeat('0',64)
                          WHERE provider_job_id=CAST(:job_id AS uuid)
                            AND review_status='READY_FOR_HUMAN_REVIEW'
                        """),
                        {"job_id": job_id},
                    )

            changed = brief.model_copy(
                update={
                    "premise": (
                        "A changed Step 7 premise must invalidate QC, package, "
                        "episode bundle, and human review package."
                    )
                }
            )
            update_shrimp_content_brief(
                job_id,
                changed,
                actor="ci-shrimp-step7",
            )

            invalidated = get_provider_job(job_id)
            invalidated_status = {
                row["stage_key"]: row["stage_status"]
                for row in invalidated["stages"]
            }
            assert invalidated_status["QC"] == "STALE"
            assert invalidated_status["PACKAGE"] == "STALE"
            assert list_episode_bundles(job_id) == []
            assert list_release_review_packages(job_id) == []

            bundle_history = list_episode_bundles(
                job_id,
                include_stale=True,
            )
            review_history = list_release_review_packages(
                job_id,
                include_stale=True,
            )
            assert len(bundle_history) == 1
            assert bundle_history[0]["bundle_status"] == "STALE"
            assert len(review_history) == 1
            assert review_history[0]["review_status"] == "STALE"

            stale_meta = get_shrimp_animation_job(job_id)[
                "shrimp_animation"
            ]
            assert stale_meta["episode_bundle_sha256"] is None
            assert stale_meta["release_review_package_sha256"] is None
            assert stale_meta["review_status"] == "STALE"
    finally:
        if opportunity_id is not None:
            with engine.begin() as db:
                db.execute(
                    text("""
                      DELETE FROM digital_asset_opportunities
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": str(opportunity_id)},
                )
        _cleanup_registry()
