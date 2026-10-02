from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import engine
from app.production_provider_contract import get_provider_job
from app.providers.animation.models import AnimationTimelineManifest
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.adapters.remotion_render import (
    RemotionRenderRequest,
    RenderAdapterResult,
)
from app.providers.animation.shrimp.animation_execution import execute_animation_stage
from app.providers.animation.shrimp.execution import (
    execute_asset_stage,
    execute_voice_stage,
)
from app.providers.animation.shrimp.provider import (
    create_shrimp_animation_job,
    get_shrimp_animation_job,
    run_deterministic_planning,
    update_shrimp_content_brief,
)
from app.providers.animation.shrimp.qc_execution import (
    analyze_qc,
    execute_qc_stage,
    list_qc_reports,
    validate_timeline_integrity,
)
from app.providers.animation.shrimp.render_execution import (
    execute_render_stage,
    list_render_artifacts,
)
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FixtureQcRenderAdapter:
    adapter_key = "FIXTURE_QC_FFMPEG"
    adapter_version = "ci-v1"

    def __init__(self, root: str):
        self.root = Path(root)
        self.calls = 0

    def render(self, request: RemotionRenderRequest) -> RenderAdapterResult:
        self.calls += 1
        output = self.root / (
            f"{request.episode_id}-{request.animation_manifest_sha256[:12]}.mp4"
        )
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            (
                f"testsrc2=size={request.expected_width}x"
                f"{request.expected_height}:rate={request.expected_fps}"
            ),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000",
            "-frames:v",
            str(request.expected_frame_count),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ]
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr)
        return RenderAdapterResult(
            artifact_path=str(output),
            adapter_key=self.adapter_key,
            adapter_version=self.adapter_version,
            command_sha256=hashlib.sha256(
                ("\0".join(command)).encode("utf-8")
            ).hexdigest(),
            stdout_tail=(completed.stdout or "")[-2000:],
            stderr_tail=(completed.stderr or "")[-2000:],
        )


def _bad_black_silent_mp4(path: Path, *, frames: int, fps: int) -> None:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s=640x360:r={fps}",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=mono:sample_rate=48000",
        "-frames:v",
        str(frames),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(command, check=True, timeout=120)


def _current_timeline(job_id) -> AnimationTimelineManifest:
    with engine.connect() as db:
        content = db.execute(
            text("""
              SELECT content
              FROM production_provider_manifests
              WHERE job_id=CAST(:job_id AS uuid)
                AND stage_key='ANIMATION'
                AND manifest_kind='animation_timeline_manifest'
                AND is_current=true
            """),
            {"job_id": job_id},
        ).scalar_one()
    return AnimationTimelineManifest.model_validate(content["timeline"])


def test_step6_qc_gate_pass_reject_replay_and_invalidation():
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
            requested_by="ci-shrimp-step6",
        )
        job_id = created["job_id"]

        run_deterministic_planning(job_id)
        run_resource_planning(job_id)
        execute_asset_stage(
            job_id,
            adapter=NoGenerationAssetAdapter(),
            resolver=FixtureResolver(reusable_media),
            actor="ci-assets-step6",
        )
        execute_voice_stage(
            job_id,
            adapter=FixtureVoiceAdapter(),
            actor="ci-voices-step6",
        )
        execute_animation_stage(
            job_id,
            adapter=FixtureRemotionAdapter(),
            actor="ci-animation-step6",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            renderer = FixtureQcRenderAdapter(temp_dir)
            rendered = execute_render_stage(
                job_id,
                adapter=renderer,
                actor="ci-render-step6",
            )
            assert rendered["stage_status"] == "SUCCEEDED"
            assert rendered["frame_count"] == 360
            assert renderer.calls == 1

            timeline = _current_timeline(job_id)
            assert timeline.total_duration_frames == 360

            timeline_checks = validate_timeline_integrity(timeline)
            assert all(check["passed"] for check in timeline_checks)

            broken_timeline = timeline.model_copy(deep=True)
            broken_timeline.scenes[0].subtitles[0].line_id = "broken-line-id"
            broken_checks = {
                check["key"]: check
                for check in validate_timeline_integrity(broken_timeline)
            }
            assert broken_checks["dialogue_subtitle_alignment"]["passed"] is False
            assert broken_checks["subtitle_audio_bijection"]["passed"] is False

            current_render = list_render_artifacts(job_id)[0]
            bad_path = Path(temp_dir) / "bad-black-silent.mp4"
            _bad_black_silent_mp4(
                bad_path,
                frames=timeline.total_duration_frames,
                fps=timeline.fps,
            )
            bad_render = dict(current_render)
            bad_render["artifact_uri"] = bad_path.resolve().as_uri()
            bad_render["artifact_sha256"] = _sha256_file(bad_path)
            bad_render["byte_size"] = bad_path.stat().st_size
            bad_report = analyze_qc(timeline, bad_render)
            bad = {
                check["key"]: check
                for check in bad_report["checks"]
            }
            assert bad_report["passed"] is False
            assert bad["black_frame_gate"]["passed"] is False
            assert bad["dialogue_audio_silence_gate"]["passed"] is False
            assert bad["video_resolution"]["passed"] is False

            original_render_sha = current_render["artifact_sha256"]
            with engine.begin() as db:
                db.execute(
                    text("""
                      UPDATE shrimp_animation_jobs
                      SET render_artifact_sha256=repeat('0',64)
                      WHERE provider_job_id=CAST(:job_id AS uuid)
                    """),
                    {"job_id": job_id},
                )
            with pytest.raises(RuntimeError, match="render artifact hash drift"):
                execute_qc_stage(
                    job_id,
                    actor="ci-qc-step6-lineage-reject",
                )
            pending = get_provider_job(job_id)
            pending_status = {
                row["stage_key"]: row["stage_status"]
                for row in pending["stages"]
            }
            assert pending_status["QC"] == "PENDING"

            with engine.begin() as db:
                db.execute(
                    text("""
                      UPDATE shrimp_animation_jobs
                      SET render_artifact_sha256=:sha
                      WHERE provider_job_id=CAST(:job_id AS uuid)
                    """),
                    {"job_id": job_id, "sha": original_render_sha},
                )

            result = execute_qc_stage(
                job_id,
                actor="ci-qc-step6",
            )
            assert result["stage_status"] == "SUCCEEDED"
            assert result["job_status"] == "QC_PASSED"
            assert result["hard_failure_count"] == 0
            assert result["next_stage"] == "PACKAGING_REVIEW"
            assert result["publish_enabled"] is False
            assert result["production_execution_enabled"] is False

            job = get_provider_job(job_id)
            statuses = {
                row["stage_key"]: row["stage_status"]
                for row in job["stages"]
            }
            assert statuses["RENDER"] == "SUCCEEDED"
            assert statuses["QC"] == "SUCCEEDED"
            assert job["job_status"] == "QC_PASSED"
            assert job["production_execution_enabled"] is False
            assert job["publish_enabled"] is False

            meta = get_shrimp_animation_job(job_id)["shrimp_animation"]
            assert meta["qc_report_sha256"] == result["qc_report_sha256"]

            reports = list_qc_reports(job_id)
            assert len(reports) == 1
            report = reports[0]
            assert report["passed"] is True
            assert report["hard_failure_count"] == 0
            assert report["qc_status"] == "PASSED"
            assert report["report_content"]["passed"] is True
            assert all(
                check["passed"]
                for check in report["report_content"]["checks"]
            )

            replay = execute_qc_stage(
                job_id,
                actor="ci-qc-step6",
            )
            assert replay["replayed"] is True
            assert replay["qc_report_sha256"] == result["qc_report_sha256"]
            assert len(list_qc_reports(job_id)) == 1

            with pytest.raises(Exception):
                with engine.begin() as db:
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_qc_reports
                          SET report_sha256=repeat('0',64)
                          WHERE provider_job_id=CAST(:job_id AS uuid)
                            AND qc_status='PASSED'
                        """),
                        {"job_id": job_id},
                    )

            changed = brief.model_copy(
                update={
                    "premise": (
                        "A changed Step 6 premise must invalidate the verified "
                        "render and its passed QC report deterministically."
                    )
                }
            )
            update_shrimp_content_brief(
                job_id,
                changed,
                actor="ci-shrimp-step6",
            )
            invalidated = get_provider_job(job_id)
            invalidated_status = {
                row["stage_key"]: row["stage_status"]
                for row in invalidated["stages"]
            }
            assert invalidated_status["ANIMATION"] == "STALE"
            assert invalidated_status["RENDER"] == "STALE"
            assert invalidated_status["QC"] == "STALE"
            assert list_qc_reports(job_id) == []

            history = list_qc_reports(job_id, include_history=True)
            passed_history = [
                row for row in history
                if row["passed"] is True
            ]
            assert len(passed_history) == 1
            assert passed_history[0]["qc_status"] == "STALE"

            stale_meta = get_shrimp_animation_job(job_id)[
                "shrimp_animation"
            ]
            assert stale_meta["render_artifact_sha256"] is None
            assert stale_meta["qc_report_sha256"] is None
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
