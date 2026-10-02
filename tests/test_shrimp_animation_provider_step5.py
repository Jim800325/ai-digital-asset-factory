from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import engine
from app.production_provider_contract import get_provider_job
from app.providers.animation.models import canonical_json
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.adapters.remotion import (
    _project_source_sha256,
)
from app.providers.animation.shrimp.adapters.remotion_render import (
    ControlledRemotionRenderAdapter,
    RemotionRenderRequest,
    RenderAdapterResult,
)
from app.providers.animation.shrimp.animation_execution import (
    execute_animation_stage,
)
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
from app.providers.animation.shrimp.render_execution import (
    execute_render_stage,
    list_render_artifacts,
    verify_mp4_artifact,
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


class FixtureFfmpegRenderAdapter:
    adapter_key = "FIXTURE_FFMPEG"
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
                f"color=c=black:s={request.expected_width}x"
                f"{request.expected_height}:r={request.expected_fps}"
            ),
            "-frames:v",
            str(request.expected_frame_count),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
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


def test_step5_render_stage_mp4_verification_replay_and_invalidation():
    opportunity_id = None
    try:
        register_shrimp_animation_provider()
        reusable_media = _seed_registry()
        opportunity_id, proposal_id = _create_proposal()

        created = create_shrimp_animation_job(
            proposal_id,
            _brief(),
            requested_by="ci-shrimp-step5",
        )
        job_id = created["job_id"]
        run_deterministic_planning(job_id)
        run_resource_planning(job_id)
        execute_asset_stage(
            job_id,
            adapter=NoGenerationAssetAdapter(),
            resolver=FixtureResolver(reusable_media),
            actor="ci-assets-step5",
        )
        execute_voice_stage(
            job_id,
            adapter=FixtureVoiceAdapter(),
            actor="ci-voices-step5",
        )
        execute_animation_stage(
            job_id,
            adapter=FixtureRemotionAdapter(),
            actor="ci-animation-step5",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            renderer = FixtureFfmpegRenderAdapter(temp_dir)
            result = execute_render_stage(
                job_id,
                adapter=renderer,
                actor="ci-render-step5",
            )

            assert result["stage_status"] == "SUCCEEDED"
            assert result["width"] == 1920
            assert result["height"] == 1080
            assert result["fps"] == pytest.approx(30.0)
            assert result["frame_count"] == 1350
            assert result["duration_ms"] == pytest.approx(45000, abs=70)
            assert len(result["artifact_sha256"]) == 64
            assert result["next_stage"] == "QC"
            assert renderer.calls == 1

            job = get_provider_job(job_id)
            statuses = {
                row["stage_key"]: row["stage_status"]
                for row in job["stages"]
            }
            assert statuses["ANIMATION"] == "SUCCEEDED"
            assert statuses["RENDER"] == "SUCCEEDED"
            assert statuses["QC"] == "PENDING"
            assert job["production_execution_enabled"] is False
            assert job["publish_enabled"] is False

            meta = get_shrimp_animation_job(job_id)["shrimp_animation"]
            assert meta["render_artifact_sha256"] == result["artifact_sha256"]

            rows = list_render_artifacts(job_id)
            assert len(rows) == 1
            row = rows[0]
            assert row["artifact_sha256"] == result["artifact_sha256"]
            assert row["animation_manifest_sha256"] == meta[
                "animation_manifest_sha256"
            ]
            assert row["props_sha256"] == meta["remotion_props_sha256"]
            assert row["render_adapter_key"] == "FIXTURE_FFMPEG"
            assert row["render_status"] == "CURRENT"
            assert row["provenance"]["verification"][
                "ffprobe_verified_by_stage"
            ] is True

            replay = execute_render_stage(
                job_id,
                adapter=renderer,
                actor="ci-render-step5",
            )
            assert replay["replayed"] is True
            assert replay["artifact_sha256"] == result["artifact_sha256"]
            assert renderer.calls == 1

            with pytest.raises(Exception):
                with engine.begin() as db:
                    db.execute(
                        text("""
                          UPDATE shrimp_animation_renders
                          SET artifact_sha256=repeat('0',64)
                          WHERE provider_job_id=CAST(:job_id AS uuid)
                            AND render_status='CURRENT'
                        """),
                        {"job_id": job_id},
                    )

            changed = _brief(
                "A revised harbor incident changes every downstream render input "
                "and must stale the previously verified MP4 artifact."
            )
            update_shrimp_content_brief(
                job_id,
                changed,
                actor="ci-shrimp-step5",
            )
            invalidated = get_provider_job(job_id)
            invalidated_status = {
                row["stage_key"]: row["stage_status"]
                for row in invalidated["stages"]
            }
            assert invalidated_status["ANIMATION"] == "STALE"
            assert invalidated_status["RENDER"] == "STALE"
            assert invalidated_status["QC"] == "STALE"
            assert list_render_artifacts(job_id) == []
            history = list_render_artifacts(job_id, include_stale=True)
            assert len(history) == 1
            assert history[0]["render_status"] == "STALE"
            stale_meta = get_shrimp_animation_job(job_id)[
                "shrimp_animation"
            ]
            assert stale_meta["render_artifact_sha256"] is None
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


def test_step5_ffprobe_rejects_frame_count_drift(tmp_path):
    output = tmp_path / "drift.mp4"
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x240:r=12",
        "-frames:v",
        "12",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    subprocess.run(command, check=True, timeout=60)
    with pytest.raises(ValueError, match="frame count mismatch"):
        verify_mp4_artifact(
            str(output),
            expected_width=320,
            expected_height=240,
            expected_fps=12,
            expected_frame_count=13,
        )


@pytest.mark.skipif(
    os.getenv("SHRIMP_STEP5_REAL_REMOTION_SMOKE") != "1",
    reason="real Remotion smoke is enabled only in CI acceptance",
)
def test_step5_real_remotion_cli_generates_verified_mp4(tmp_path):
    project_dir = Path("renderer/remotion").resolve()
    cli = project_dir / "node_modules" / ".bin" / "remotion"
    if not cli.is_file():
        pytest.fail("Pinned Remotion CLI was not installed for Step 5 acceptance")

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240">'
        '<rect width="320" height="240" fill="#111827"/>'
        '<text x="160" y="125" text-anchor="middle" fill="white" '
        'font-size="28">Step 5</text></svg>'
    )
    background_uri = (
        "data:image/svg+xml;base64,"
        + base64.b64encode(svg.encode("utf-8")).decode("ascii")
    )
    props = {
        "animation": {
            "schema_version": "animation-timeline-v0.1",
            "planner_version": "animation-planner-v0.1-deterministic",
            "episode_id": "step5-real-remotion-smoke",
            "fps": 12,
            "resolution": {"width": 320, "height": 240},
            "scene_manifest_sha256": "1" * 64,
            "asset_manifest_sha256": "2" * 64,
            "voice_manifest_sha256": "3" * 64,
            "total_duration_frames": 12,
            "scenes": [
                {
                    "scene_id": "s001",
                    "start_frame": 0,
                    "end_frame": 12,
                    "duration_frames": 12,
                    "background": {
                        "logical_key": "background:smoke",
                        "storage_uri": background_uri,
                        "media_type": "image/svg+xml",
                        "sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
                    },
                    "transition": "cut",
                    "camera": [
                        {
                            "type": "static",
                            "start_frame": 0,
                            "end_frame": 12,
                            "from_scale": 1.0,
                            "to_scale": 1.0,
                        }
                    ],
                    "characters": [],
                    "audio": [],
                    "subtitles": [],
                }
            ],
        }
    }

    props_root = tmp_path / "props"
    output_root = tmp_path / "renders"
    props_root.mkdir()
    encoded = canonical_json(props).encode("utf-8")
    props_sha = hashlib.sha256(encoded).hexdigest()
    props_path = props_root / f"{props_sha}.json"
    props_path.write_bytes(encoded)

    project_sha = _project_source_sha256(project_dir)
    adapter = ControlledRemotionRenderAdapter(
        project_dir=str(project_dir),
        props_allowed_root=str(props_root),
        output_root=str(output_root),
        remotion_cli=str(cli),
        timeout_seconds=300,
    )
    request = RemotionRenderRequest(
        episode_id="step5-real-remotion-smoke",
        composition_record_id="00000000-0000-0000-0000-000000000001",
        composition_id="ShrimpAnimation",
        animation_manifest_sha256="4" * 64,
        props_sha256=props_sha,
        project_source_sha256=project_sha,
        props_uri=props_path.as_uri(),
        expected_width=320,
        expected_height=240,
        expected_fps=12,
        expected_frame_count=12,
    )

    rendered = adapter.render(request)
    verified = verify_mp4_artifact(
        rendered.artifact_path,
        expected_width=320,
        expected_height=240,
        expected_fps=12,
        expected_frame_count=12,
    )
    assert verified.width == 320
    assert verified.height == 240
    assert verified.fps == pytest.approx(12.0)
    assert verified.frame_count == 12
    assert verified.duration_ms == pytest.approx(1000, abs=170)
    assert verified.byte_size > 0
    assert len(verified.artifact_sha256) == 64
    assert rendered.adapter_key == "REMOTION_CONTROLLED"
