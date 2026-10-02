from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path

from sqlalchemy import text

from app.db import engine
from app.production_provider_contract import (
    ProviderManifestEnvelope,
    complete_provider_stage,
    fail_provider_stage,
    get_provider_job,
    start_provider_stage,
    write_provider_manifest,
)
from app.providers.animation.models import AnimationTimelineManifest, canonical_json
from app.providers.animation.shrimp.adapters.remotion_render import (
    RemotionRenderRequest,
    VideoRenderAdapter,
)


@dataclass(frozen=True)
class VerifiedMp4:
    artifact_path: str
    artifact_uri: str
    artifact_sha256: str
    byte_size: int
    media_type: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_ms: int
    probe: dict


def _stage_status(job: dict, key: str) -> str:
    return next(
        stage["stage_status"]
        for stage in job["stages"]
        if stage["stage_key"] == key
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_rate(value: str) -> float:
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"Invalid ffprobe frame rate: {value}") from exc


def verify_mp4_artifact(
    artifact_path: str,
    *,
    expected_width: int,
    expected_height: int,
    expected_fps: int,
    expected_frame_count: int,
    ffprobe_cli: str = "ffprobe",
) -> VerifiedMp4:
    path = Path(artifact_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("Rendered MP4 artifact is missing")
    byte_size = path.stat().st_size
    if byte_size <= 0:
        raise ValueError("Rendered MP4 artifact is empty")
    if path.suffix.lower() != ".mp4":
        raise ValueError("Rendered artifact must use .mp4")

    command = [
        ffprobe_cli,
        "-v",
        "error",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,nb_read_frames",
        "-show_entries",
        "format=duration,format_name",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(
            "ffprobe rejected rendered MP4: "
            + (completed.stderr or "")[-2000:]
        )
    try:
        probe = json.loads(completed.stdout)
        stream = probe["streams"][0]
        fmt = probe["format"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("ffprobe returned incomplete MP4 metadata") from exc

    format_names = {
        item.strip()
        for item in str(fmt.get("format_name") or "").split(",")
        if item.strip()
    }
    if not {"mov", "mp4"}.intersection(format_names):
        raise ValueError("Rendered artifact is not an MP4 container")

    width = int(stream["width"])
    height = int(stream["height"])
    fps = _parse_rate(str(stream["avg_frame_rate"]))
    frame_count = int(stream["nb_read_frames"])
    duration_ms = int(round(float(fmt["duration"]) * 1000))

    if width != int(expected_width) or height != int(expected_height):
        raise ValueError(
            f"MP4 resolution mismatch: {width}x{height} != "
            f"{expected_width}x{expected_height}"
        )
    if not math.isclose(fps, float(expected_fps), rel_tol=0.0, abs_tol=0.001):
        raise ValueError(f"MP4 FPS mismatch: {fps} != {expected_fps}")
    if frame_count != int(expected_frame_count):
        raise ValueError(
            f"MP4 frame count mismatch: {frame_count} != {expected_frame_count}"
        )

    expected_duration_ms = int(
        round(int(expected_frame_count) * 1000 / int(expected_fps))
    )
    tolerance_ms = max(50, int(math.ceil(2000 / int(expected_fps))))
    if abs(duration_ms - expected_duration_ms) > tolerance_ms:
        raise ValueError(
            f"MP4 duration mismatch: {duration_ms}ms != "
            f"{expected_duration_ms}ms (+/- {tolerance_ms}ms)"
        )

    return VerifiedMp4(
        artifact_path=str(path),
        artifact_uri=path.as_uri(),
        artifact_sha256=_sha256_file(path),
        byte_size=byte_size,
        media_type="video/mp4",
        width=width,
        height=height,
        fps=fps,
        frame_count=frame_count,
        duration_ms=duration_ms,
        probe=probe,
    )


def _current_animation(job_id) -> tuple[dict, str]:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT content,content_sha256
              FROM production_provider_manifests
              WHERE job_id=CAST(:job_id AS uuid)
                AND stage_key='ANIMATION'
                AND manifest_kind='animation_timeline_manifest'
                AND is_current=true
              ORDER BY manifest_version DESC
              LIMIT 1
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
    if row is None:
        raise RuntimeError("Current ANIMATION manifest is missing")
    return row["content"], row["content_sha256"]


def _current_composition(job_id) -> dict:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT id,animation_manifest_sha256,renderer_key,renderer_version,
                     composition_id,project_source_sha256,props_uri,props_sha256,
                     composition_status
              FROM shrimp_animation_compositions
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND composition_status='CURRENT'
              ORDER BY created_at DESC
              LIMIT 1
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
    if row is None:
        raise RuntimeError("Current Remotion composition is missing")
    return dict(row)


def _current_render(job_id) -> dict | None:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_renders
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND render_status='CURRENT'
              ORDER BY created_at DESC
              LIMIT 1
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
    return dict(row) if row else None


def _validate_frozen_inputs(job_id):
    job = get_provider_job(job_id)
    if _stage_status(job, "ANIMATION") != "SUCCEEDED":
        raise RuntimeError("ANIMATION must be SUCCEEDED before RENDER")

    animation, animation_sha = _current_animation(job_id)
    timeline = AnimationTimelineManifest.model_validate(animation["timeline"])
    remotion = animation["remotion"]
    composition = _current_composition(job_id)

    if composition["animation_manifest_sha256"] != animation_sha:
        raise RuntimeError("Composition is not bound to current ANIMATION manifest")
    if composition["props_sha256"] != remotion["props_sha256"]:
        raise RuntimeError("Composition props hash drift detected")
    if composition["project_source_sha256"] != remotion["project_source_sha256"]:
        raise RuntimeError("Composition project source hash drift detected")
    if composition["composition_id"] != remotion["composition_id"]:
        raise RuntimeError("Composition ID drift detected")

    with engine.connect() as db:
        meta = db.execute(
            text("""
              SELECT animation_manifest_sha256,remotion_props_sha256
              FROM shrimp_animation_jobs
              WHERE provider_job_id=CAST(:job_id AS uuid)
            """),
            {"job_id": job_id},
        ).mappings().one()
    if meta["animation_manifest_sha256"] != animation_sha:
        raise RuntimeError("Job animation_manifest_sha256 drift detected")
    if meta["remotion_props_sha256"] != composition["props_sha256"]:
        raise RuntimeError("Job remotion_props_sha256 drift detected")

    return job, timeline, animation_sha, composition


def _persist_render(
    job_id,
    *,
    composition: dict,
    animation_manifest_sha256: str,
    verified: VerifiedMp4,
    adapter_key: str,
    adapter_version: str,
    command_sha256: str,
    provenance: dict,
) -> dict:
    with engine.begin() as db:
        current = db.execute(
            text("""
              SELECT id,animation_manifest_sha256,props_sha256,
                     project_source_sha256,artifact_sha256
              FROM shrimp_animation_renders
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND render_status='CURRENT'
              FOR UPDATE
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()

        if (
            current
            and current["animation_manifest_sha256"] == animation_manifest_sha256
            and current["props_sha256"] == composition["props_sha256"]
            and current["project_source_sha256"]
                == composition["project_source_sha256"]
            and current["artifact_sha256"] == verified.artifact_sha256
        ):
            return {"render_id": str(current["id"]), "changed": False}

        if current:
            db.execute(
                text("""
                  UPDATE shrimp_animation_renders
                  SET render_status='STALE',
                      superseded_at=COALESCE(superseded_at,now())
                  WHERE id=:id
                """),
                {"id": current["id"]},
            )

        render_id = db.execute(
            text("""
              INSERT INTO shrimp_animation_renders(
                provider_job_id,composition_record_id,
                animation_manifest_sha256,props_sha256,project_source_sha256,
                render_adapter_key,render_adapter_version,command_sha256,
                artifact_uri,artifact_sha256,byte_size,media_type,
                width,height,fps,frame_count,duration_ms,
                probe_content,provenance,render_status)
              VALUES(
                CAST(:job_id AS uuid),CAST(:composition_id AS uuid),
                :animation_sha,:props_sha,:project_sha,
                :adapter_key,:adapter_version,:command_sha,
                :artifact_uri,:artifact_sha,:byte_size,:media_type,
                :width,:height,:fps,:frame_count,:duration_ms,
                CAST(:probe AS jsonb),CAST(:provenance AS jsonb),'CURRENT')
              RETURNING id
            """),
            {
                "job_id": job_id,
                "composition_id": composition["id"],
                "animation_sha": animation_manifest_sha256,
                "props_sha": composition["props_sha256"],
                "project_sha": composition["project_source_sha256"],
                "adapter_key": adapter_key,
                "adapter_version": adapter_version,
                "command_sha": command_sha256,
                "artifact_uri": verified.artifact_uri,
                "artifact_sha": verified.artifact_sha256,
                "byte_size": verified.byte_size,
                "media_type": verified.media_type,
                "width": verified.width,
                "height": verified.height,
                "fps": verified.fps,
                "frame_count": verified.frame_count,
                "duration_ms": verified.duration_ms,
                "probe": canonical_json(verified.probe),
                "provenance": canonical_json(provenance),
            },
        ).scalar_one()

    return {"render_id": str(render_id), "changed": True}


def execute_render_stage(
    job_id,
    *,
    adapter: VideoRenderAdapter,
    ffprobe_cli: str = "ffprobe",
    actor: str = "shrimp-render-worker",
) -> dict:
    job, timeline, animation_sha, composition = _validate_frozen_inputs(job_id)

    if _stage_status(job, "RENDER") == "SUCCEEDED":
        current = _current_render(job_id)
        if current is None:
            raise RuntimeError("RENDER succeeded without a current render artifact")
        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "artifact_sha256": current["artifact_sha256"],
            "artifact_uri": current["artifact_uri"],
            "render_id": str(current["id"]),
            "replayed": True,
            "next_stage": "QC",
        }

    start_provider_stage(job_id, "RENDER", actor=actor)
    try:
        request = RemotionRenderRequest(
            episode_id=timeline.episode_id,
            composition_record_id=str(composition["id"]),
            composition_id=composition["composition_id"],
            animation_manifest_sha256=animation_sha,
            props_sha256=composition["props_sha256"],
            project_source_sha256=composition["project_source_sha256"],
            props_uri=composition["props_uri"],
            expected_width=timeline.resolution.width,
            expected_height=timeline.resolution.height,
            expected_fps=timeline.fps,
            expected_frame_count=timeline.total_duration_frames,
        )
        rendered = adapter.render(request)
        verified = verify_mp4_artifact(
            rendered.artifact_path,
            expected_width=request.expected_width,
            expected_height=request.expected_height,
            expected_fps=request.expected_fps,
            expected_frame_count=request.expected_frame_count,
            ffprobe_cli=ffprobe_cli,
        )

        provenance = {
            "stage": "RENDER",
            "provider": "shrimp_animation",
            "adapter_key": rendered.adapter_key,
            "adapter_version": rendered.adapter_version,
            "composition_record_id": request.composition_record_id,
            "animation_manifest_sha256": request.animation_manifest_sha256,
            "props_sha256": request.props_sha256,
            "project_source_sha256": request.project_source_sha256,
            "command_sha256": rendered.command_sha256,
            "verification": {
                "sha256_computed_by_stage": True,
                "ffprobe_verified_by_stage": True,
                "resolution_verified": True,
                "fps_verified": True,
                "frame_count_verified": True,
                "duration_verified": True,
            },
        }

        persisted = _persist_render(
            job_id,
            composition=composition,
            animation_manifest_sha256=animation_sha,
            verified=verified,
            adapter_key=rendered.adapter_key,
            adapter_version=rendered.adapter_version,
            command_sha256=rendered.command_sha256,
            provenance=provenance,
        )

        manifest_payload = {
            "artifact": {
                "uri": verified.artifact_uri,
                "media_type": verified.media_type,
                "sha256": verified.artifact_sha256,
                "byte_size": verified.byte_size,
                "width": verified.width,
                "height": verified.height,
                "fps": verified.fps,
                "frame_count": verified.frame_count,
                "duration_ms": verified.duration_ms,
            },
            "frozen_inputs": {
                "animation_manifest_sha256": animation_sha,
                "props_sha256": composition["props_sha256"],
                "project_source_sha256": composition["project_source_sha256"],
                "composition_record_id": str(composition["id"]),
            },
            "provenance": provenance,
        }
        written = write_provider_manifest(
            job_id,
            "RENDER",
            ProviderManifestEnvelope(
                manifest_kind="render_artifact_manifest",
                schema_version="render-artifact-v0.1",
                payload=manifest_payload,
            ),
            actor=actor,
        )
        complete_provider_stage(job_id, "RENDER", actor=actor)

        with engine.begin() as db:
            db.execute(
                text("""
                  UPDATE shrimp_animation_jobs
                  SET render_artifact_sha256=:artifact_sha,
                      updated_at=now()
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                """),
                {
                    "job_id": job_id,
                    "artifact_sha": verified.artifact_sha256,
                },
            )

        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "render_manifest_sha256": written["content_sha256"],
            "artifact_sha256": verified.artifact_sha256,
            "artifact_uri": verified.artifact_uri,
            "byte_size": verified.byte_size,
            "width": verified.width,
            "height": verified.height,
            "fps": verified.fps,
            "frame_count": verified.frame_count,
            "duration_ms": verified.duration_ms,
            "render_id": persisted["render_id"],
            "render_changed": persisted["changed"],
            "replayed": False,
            "next_stage": "QC",
        }
    except Exception as exc:
        fail_provider_stage(
            job_id,
            "RENDER",
            str(exc),
            retryable=not isinstance(
                exc,
                (
                    ValueError,
                    PermissionError,
                    FileNotFoundError,
                    LookupError,
                ),
            ),
            actor=actor,
        )
        raise


def list_render_artifacts(
    job_id,
    *,
    include_stale: bool = False,
) -> list[dict]:
    sql = """
      SELECT id,composition_record_id,animation_manifest_sha256,
             props_sha256,project_source_sha256,render_adapter_key,
             render_adapter_version,command_sha256,artifact_uri,
             artifact_sha256,byte_size,media_type,width,height,fps,
             frame_count,duration_ms,probe_content,provenance,
             render_status,created_at,superseded_at
      FROM shrimp_animation_renders
      WHERE provider_job_id=CAST(:job_id AS uuid)
    """
    if not include_stale:
        sql += " AND render_status='CURRENT'"
    sql += " ORDER BY created_at DESC"
    with engine.connect() as db:
        return [
            dict(row)
            for row in db.execute(
                text(sql),
                {"job_id": job_id},
            ).mappings().all()
        ]
