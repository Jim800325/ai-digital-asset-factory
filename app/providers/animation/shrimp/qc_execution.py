from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

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


QC_ANALYZER_VERSION = "shrimp-qc-v0.1-deterministic"


@dataclass(frozen=True)
class QCThresholds:
    max_black_segment_ms: int = 1500
    max_black_ratio: float = 0.10
    max_freeze_segment_ms: int = 8000
    max_freeze_ratio: float = 0.50
    max_dialogue_silence_ratio: float = 0.80
    analysis_timeout_seconds: float = 180.0


class QCRejected(ValueError):
    def __init__(self, report: dict):
        failures = [
            check["key"]
            for check in report.get("checks", [])
            if check.get("severity") == "HARD" and not check.get("passed")
        ]
        super().__init__(
            "Shrimp animation QC rejected: "
            + (", ".join(failures) if failures else "hard gate failed")
        )
        self.report = report


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
        raise ValueError(f"Invalid media frame rate: {value}") from exc


def _artifact_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise PermissionError("QC currently accepts only local file:// render artifacts")
    if parsed.netloc not in ("", "localhost"):
        raise PermissionError("QC does not allow remote file URI hosts")
    return Path(url2pathname(unquote(parsed.path))).resolve()


def _run(
    command: list[str],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"QC media command failed (exit={completed.returncode}): "
            + (completed.stderr or "")[-2000:]
        )
    return completed


def _interval_total(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def _overlap_total(
    intervals: list[tuple[float, float]],
    window: tuple[float, float],
) -> float:
    start, end = window
    if end <= start:
        return 0.0
    clipped = [
        (max(start, a), min(end, b))
        for a, b in intervals
        if min(end, b) > max(start, a)
    ]
    return _interval_total(clipped)


def _parse_black_intervals(log: str) -> list[tuple[float, float]]:
    pattern = re.compile(
        r"black_start:(?P<start>[0-9.]+).*?"
        r"black_end:(?P<end>[0-9.]+).*?"
        r"black_duration:(?P<duration>[0-9.]+)"
    )
    return [
        (float(match.group("start")), float(match.group("end")))
        for match in pattern.finditer(log)
    ]


def _parse_tagged_intervals(
    log: str,
    *,
    prefix: str,
    media_duration_seconds: float,
) -> list[tuple[float, float]]:
    start_re = re.compile(rf"{re.escape(prefix)}_start:\s*([0-9.]+)")
    end_re = re.compile(rf"{re.escape(prefix)}_end:\s*([0-9.]+)")
    starts = [float(value) for value in start_re.findall(log)]
    ends = [float(value) for value in end_re.findall(log)]
    intervals: list[tuple[float, float]] = []
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else media_duration_seconds
        if end > start:
            intervals.append((start, min(end, media_duration_seconds)))
    return intervals


def _timeline_dialogue_windows(
    timeline: AnimationTimelineManifest,
) -> list[dict]:
    windows: list[dict] = []
    for scene in timeline.scenes:
        subtitles = {cue.line_id: cue for cue in scene.subtitles}
        for audio in scene.audio:
            subtitle = subtitles.get(audio.line_id)
            windows.append(
                {
                    "scene_id": scene.scene_id,
                    "line_id": audio.line_id,
                    "speaker": audio.speaker,
                    "start_frame": scene.start_frame + audio.start_frame,
                    "end_frame": scene.start_frame + audio.end_frame,
                    "start_seconds": (
                        scene.start_frame + audio.start_frame
                    ) / timeline.fps,
                    "end_seconds": (
                        scene.start_frame + audio.end_frame
                    ) / timeline.fps,
                    "subtitle_present": subtitle is not None,
                    "subtitle_aligned": bool(
                        subtitle is not None
                        and subtitle.speaker == audio.speaker
                        and subtitle.start_frame == audio.start_frame
                        and subtitle.end_frame == audio.end_frame
                        and bool(subtitle.text.strip())
                    ),
                }
            )
    return windows


def validate_timeline_integrity(
    timeline: AnimationTimelineManifest,
) -> list[dict]:
    checks: list[dict] = []

    def add(key: str, passed: bool, observed, expected, details: str = ""):
        checks.append(
            {
                "key": key,
                "severity": "HARD",
                "passed": bool(passed),
                "observed": observed,
                "expected": expected,
                "details": details,
            }
        )

    cursor = 0
    contiguous = True
    for scene in timeline.scenes:
        if scene.start_frame != cursor:
            contiguous = False
            break
        cursor = scene.end_frame
    contiguous = contiguous and cursor == timeline.total_duration_frames
    add(
        "timeline_scene_contiguity",
        contiguous,
        cursor,
        timeline.total_duration_frames,
        "Scenes must be contiguous and terminate at total_duration_frames.",
    )

    windows = _timeline_dialogue_windows(timeline)
    line_ids = [window["line_id"] for window in windows]
    add(
        "dialogue_line_ids_unique",
        len(line_ids) == len(set(line_ids)),
        len(set(line_ids)),
        len(line_ids),
        "Every rendered dialogue line must have one unique line_id.",
    )

    aligned = all(
        window["subtitle_present"] and window["subtitle_aligned"]
        for window in windows
    )
    add(
        "dialogue_subtitle_alignment",
        bool(windows) and aligned,
        sum(
            window["subtitle_present"] and window["subtitle_aligned"]
            for window in windows
        ),
        len(windows),
        "Every audio cue must have an aligned non-empty subtitle cue.",
    )

    subtitle_ids = [
        subtitle.line_id
        for scene in timeline.scenes
        for subtitle in scene.subtitles
    ]
    add(
        "subtitle_audio_bijection",
        sorted(subtitle_ids) == sorted(line_ids),
        sorted(subtitle_ids),
        sorted(line_ids),
        "Subtitle and audio line_id sets must match exactly.",
    )

    in_bounds = all(
        0 <= window["start_frame"] < window["end_frame"]
        <= timeline.total_duration_frames
        for window in windows
    )
    add(
        "dialogue_windows_in_bounds",
        bool(windows) and in_bounds,
        len(windows),
        ">0 bounded dialogue windows",
    )
    return checks


def _probe_media(
    path: Path,
    *,
    ffprobe_cli: str,
    timeout_seconds: float,
) -> dict:
    completed = _run(
        [
            ffprobe_cli,
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        timeout_seconds=timeout_seconds,
    )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe returned invalid JSON during QC") from exc


def _analyze_video(
    path: Path,
    *,
    ffmpeg_cli: str,
    timeout_seconds: float,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]], str]:
    completed = _run(
        [
            ffmpeg_cli,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            (
                "blackdetect=d=0.5:pix_th=0.10,"
                "freezedetect=n=-50dB:d=2"
            ),
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout_seconds=timeout_seconds,
    )
    log = completed.stderr or ""
    return _parse_black_intervals(log), [], log


def _parse_freeze_intervals(
    log: str,
    *,
    media_duration_seconds: float,
) -> list[tuple[float, float]]:
    return _parse_tagged_intervals(
        log,
        prefix="lavfi.freezedetect.freeze",
        media_duration_seconds=media_duration_seconds,
    )


def _analyze_audio(
    path: Path,
    *,
    ffmpeg_cli: str,
    timeout_seconds: float,
    media_duration_seconds: float,
) -> tuple[list[tuple[float, float]], str]:
    completed = _run(
        [
            ffmpeg_cli,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "silencedetect=n=-45dB:d=0.25",
            "-vn",
            "-f",
            "null",
            "-",
        ],
        timeout_seconds=timeout_seconds,
    )
    log = completed.stderr or ""
    return (
        _parse_tagged_intervals(
            log,
            prefix="silence",
            media_duration_seconds=media_duration_seconds,
        ),
        log,
    )


def analyze_qc(
    timeline: AnimationTimelineManifest,
    render: dict,
    *,
    ffprobe_cli: str = "ffprobe",
    ffmpeg_cli: str = "ffmpeg",
    thresholds: QCThresholds | None = None,
) -> dict:
    thresholds = thresholds or QCThresholds()
    path = _artifact_path(render["artifact_uri"])
    if not path.is_file():
        raise FileNotFoundError("Current render artifact is missing")

    checks = validate_timeline_integrity(timeline)

    def add(key: str, passed: bool, observed, expected, details: str = ""):
        checks.append(
            {
                "key": key,
                "severity": "HARD",
                "passed": bool(passed),
                "observed": observed,
                "expected": expected,
                "details": details,
            }
        )

    artifact_sha = _sha256_file(path)
    add(
        "render_artifact_sha256",
        artifact_sha == render["artifact_sha256"],
        artifact_sha,
        render["artifact_sha256"],
    )

    probe = _probe_media(
        path,
        ffprobe_cli=ffprobe_cli,
        timeout_seconds=thresholds.analysis_timeout_seconds,
    )
    streams = list(probe.get("streams") or [])
    video_streams = [
        stream for stream in streams
        if stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream for stream in streams
        if stream.get("codec_type") == "audio"
    ]
    add("video_stream_count", len(video_streams) == 1, len(video_streams), 1)

    dialogue_windows = _timeline_dialogue_windows(timeline)
    audio_required = bool(dialogue_windows)
    add(
        "audio_stream_present",
        (not audio_required) or bool(audio_streams),
        len(audio_streams),
        ">=1" if audio_required else "optional",
    )

    media_duration_seconds = float(
        (probe.get("format") or {}).get("duration") or 0.0
    )
    expected_duration_seconds = (
        timeline.total_duration_frames / timeline.fps
    )
    add(
        "container_duration",
        abs(media_duration_seconds - expected_duration_seconds)
        <= max(0.05, 2.0 / timeline.fps),
        round(media_duration_seconds, 6),
        round(expected_duration_seconds, 6),
    )

    if video_streams:
        video = video_streams[0]
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        fps = _parse_rate(
            str(video.get("avg_frame_rate") or "0/1")
        )
        frames = int(
            video.get("nb_read_frames")
            or video.get("nb_frames")
            or 0
        )
        add(
            "video_resolution",
            width == timeline.resolution.width
            and height == timeline.resolution.height,
            f"{width}x{height}",
            f"{timeline.resolution.width}x{timeline.resolution.height}",
        )
        add(
            "video_fps",
            math.isclose(
                fps,
                float(timeline.fps),
                rel_tol=0.0,
                abs_tol=0.001,
            ),
            fps,
            timeline.fps,
        )
        add(
            "video_frame_count",
            frames == timeline.total_duration_frames,
            frames,
            timeline.total_duration_frames,
        )

    black_intervals, _, video_log = _analyze_video(
        path,
        ffmpeg_cli=ffmpeg_cli,
        timeout_seconds=thresholds.analysis_timeout_seconds,
    )
    freeze_intervals = _parse_freeze_intervals(
        video_log,
        media_duration_seconds=media_duration_seconds,
    )
    black_total = _interval_total(black_intervals)
    freeze_total = _interval_total(freeze_intervals)
    black_max = max(
        (end - start for start, end in black_intervals),
        default=0.0,
    )
    freeze_max = max(
        (end - start for start, end in freeze_intervals),
        default=0.0,
    )
    duration_base = max(media_duration_seconds, 0.001)
    black_ratio = black_total / duration_base
    freeze_ratio = freeze_total / duration_base

    add(
        "black_frame_gate",
        black_max * 1000 <= thresholds.max_black_segment_ms
        and black_ratio <= thresholds.max_black_ratio,
        {
            "max_segment_ms": round(black_max * 1000, 3),
            "ratio": round(black_ratio, 6),
            "segments": len(black_intervals),
        },
        {
            "max_segment_ms": thresholds.max_black_segment_ms,
            "max_ratio": thresholds.max_black_ratio,
        },
    )
    add(
        "freeze_frame_gate",
        freeze_max * 1000 <= thresholds.max_freeze_segment_ms
        and freeze_ratio <= thresholds.max_freeze_ratio,
        {
            "max_segment_ms": round(freeze_max * 1000, 3),
            "ratio": round(freeze_ratio, 6),
            "segments": len(freeze_intervals),
        },
        {
            "max_segment_ms": thresholds.max_freeze_segment_ms,
            "max_ratio": thresholds.max_freeze_ratio,
        },
    )

    silence_intervals: list[tuple[float, float]] = []
    if audio_streams:
        silence_intervals, _ = _analyze_audio(
            path,
            ffmpeg_cli=ffmpeg_cli,
            timeout_seconds=thresholds.analysis_timeout_seconds,
            media_duration_seconds=media_duration_seconds,
        )

    dialogue_silence = []
    for window in dialogue_windows:
        start = float(window["start_seconds"])
        end = float(window["end_seconds"])
        span = max(end - start, 0.001)
        silence = _overlap_total(silence_intervals, (start, end))
        dialogue_silence.append(
            {
                "scene_id": window["scene_id"],
                "line_id": window["line_id"],
                "silence_ratio": silence / span,
            }
        )
    max_dialogue_silence = max(
        (item["silence_ratio"] for item in dialogue_silence),
        default=0.0,
    )
    add(
        "dialogue_audio_silence_gate",
        (not audio_required)
        or (
            bool(audio_streams)
            and max_dialogue_silence
            <= thresholds.max_dialogue_silence_ratio
        ),
        {
            "max_expected_window_silence_ratio": round(
                max_dialogue_silence,
                6,
            ),
            "windows": dialogue_silence,
        },
        {
            "max_ratio": thresholds.max_dialogue_silence_ratio,
        },
        "Silence is evaluated only inside expected dialogue windows.",
    )

    hard_failures = [
        check for check in checks
        if check["severity"] == "HARD" and not check["passed"]
    ]
    return {
        "schema_version": "qc-report-v0.1",
        "analyzer_version": QC_ANALYZER_VERSION,
        "episode_id": timeline.episode_id,
        "render_id": str(render["id"]),
        "render_artifact_sha256": render["artifact_sha256"],
        "animation_manifest_sha256": render["animation_manifest_sha256"],
        "thresholds": asdict(thresholds),
        "media": {
            "artifact_uri": render["artifact_uri"],
            "byte_size": int(render["byte_size"]),
            "duration_seconds": media_duration_seconds,
            "video_streams": len(video_streams),
            "audio_streams": len(audio_streams),
            "black_intervals": black_intervals,
            "freeze_intervals": freeze_intervals,
            "silence_intervals": silence_intervals,
        },
        "checks": checks,
        "hard_failure_count": len(hard_failures),
        "passed": not hard_failures,
    }


def _current_manifest(
    job_id,
    *,
    stage_key: str,
    manifest_kind: str,
) -> tuple[dict, str]:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT content,content_sha256
              FROM production_provider_manifests
              WHERE job_id=CAST(:job_id AS uuid)
                AND stage_key=:stage_key
                AND manifest_kind=:manifest_kind
                AND is_current=true
              ORDER BY manifest_version DESC
              LIMIT 1
            """),
            {
                "job_id": job_id,
                "stage_key": stage_key,
                "manifest_kind": manifest_kind,
            },
        ).mappings().one_or_none()
    if row is None:
        raise RuntimeError(
            f"Current {stage_key}/{manifest_kind} manifest is missing"
        )
    return row["content"], row["content_sha256"]


def _current_render(job_id) -> dict:
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
    if row is None:
        raise RuntimeError("Current verified render artifact is missing")
    return dict(row)


def _current_passed_report(job_id) -> dict | None:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_qc_reports
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND qc_status='PASSED'
              ORDER BY created_at DESC
              LIMIT 1
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
    return dict(row) if row else None


def _validate_inputs(job_id):
    job = get_provider_job(job_id)
    if _stage_status(job, "RENDER") != "SUCCEEDED":
        raise RuntimeError("RENDER must be SUCCEEDED before QC")

    animation, animation_sha = _current_manifest(
        job_id,
        stage_key="ANIMATION",
        manifest_kind="animation_timeline_manifest",
    )
    render_manifest, render_manifest_sha = _current_manifest(
        job_id,
        stage_key="RENDER",
        manifest_kind="render_artifact_manifest",
    )
    timeline = AnimationTimelineManifest.model_validate(animation["timeline"])
    render = _current_render(job_id)

    frozen = render_manifest.get("frozen_inputs") or {}
    artifact = render_manifest.get("artifact") or {}
    if render["animation_manifest_sha256"] != animation_sha:
        raise RuntimeError("QC animation lineage drift detected")
    if frozen.get("animation_manifest_sha256") != animation_sha:
        raise RuntimeError("RENDER manifest animation hash drift detected")
    if frozen.get("props_sha256") != render["props_sha256"]:
        raise RuntimeError("RENDER manifest props hash drift detected")
    if frozen.get("project_source_sha256") != render["project_source_sha256"]:
        raise RuntimeError("RENDER manifest source hash drift detected")
    if str(frozen.get("composition_record_id")) != str(
        render["composition_record_id"]
    ):
        raise RuntimeError("RENDER manifest composition identity drift detected")
    if artifact.get("sha256") != render["artifact_sha256"]:
        raise RuntimeError("RENDER manifest artifact hash drift detected")
    if artifact.get("uri") != render["artifact_uri"]:
        raise RuntimeError("RENDER manifest artifact URI drift detected")

    with engine.connect() as db:
        meta = db.execute(
            text("""
              SELECT animation_manifest_sha256,render_artifact_sha256
              FROM shrimp_animation_jobs
              WHERE provider_job_id=CAST(:job_id AS uuid)
            """),
            {"job_id": job_id},
        ).mappings().one()
    if meta["animation_manifest_sha256"] != animation_sha:
        raise RuntimeError("Job animation manifest hash drift detected")
    if meta["render_artifact_sha256"] != render["artifact_sha256"]:
        raise RuntimeError("Job render artifact hash drift detected")

    return (
        job,
        timeline,
        animation_sha,
        render_manifest_sha,
        render,
    )


def _persist_report(
    job_id,
    *,
    render: dict,
    report: dict,
    passed: bool,
) -> dict:
    report_sha = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    status = "PASSED" if passed else "FAILED"
    with engine.begin() as db:
        if passed:
            current = db.execute(
                text("""
                  SELECT id,render_artifact_sha256,report_sha256
                  FROM shrimp_animation_qc_reports
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                    AND qc_status='PASSED'
                  FOR UPDATE
                """),
                {"job_id": job_id},
            ).mappings().one_or_none()
            if (
                current
                and current["render_artifact_sha256"]
                    == render["artifact_sha256"]
                and current["report_sha256"] == report_sha
            ):
                return {
                    "qc_report_id": str(current["id"]),
                    "report_sha256": report_sha,
                    "changed": False,
                }
            if current:
                db.execute(
                    text("""
                      UPDATE shrimp_animation_qc_reports
                      SET qc_status='STALE',
                          superseded_at=COALESCE(superseded_at,now())
                      WHERE id=:id
                    """),
                    {"id": current["id"]},
                )

        report_id = db.execute(
            text("""
              INSERT INTO shrimp_animation_qc_reports(
                provider_job_id,render_id,render_artifact_sha256,
                animation_manifest_sha256,analyzer_version,
                report_sha256,report_content,passed,
                hard_failure_count,qc_status)
              VALUES(
                CAST(:job_id AS uuid),CAST(:render_id AS uuid),
                :render_sha,:animation_sha,:analyzer_version,
                :report_sha,CAST(:report AS jsonb),:passed,
                :hard_failure_count,:qc_status)
              RETURNING id
            """),
            {
                "job_id": job_id,
                "render_id": render["id"],
                "render_sha": render["artifact_sha256"],
                "animation_sha": render["animation_manifest_sha256"],
                "analyzer_version": QC_ANALYZER_VERSION,
                "report_sha": report_sha,
                "report": canonical_json(report),
                "passed": passed,
                "hard_failure_count": int(report["hard_failure_count"]),
                "qc_status": status,
            },
        ).scalar_one()
    return {
        "qc_report_id": str(report_id),
        "report_sha256": report_sha,
        "changed": True,
    }


def execute_qc_stage(
    job_id,
    *,
    ffprobe_cli: str = "ffprobe",
    ffmpeg_cli: str = "ffmpeg",
    thresholds: QCThresholds | None = None,
    actor: str = "shrimp-qc-worker",
) -> dict:
    (
        job,
        timeline,
        animation_sha,
        render_manifest_sha,
        render,
    ) = _validate_inputs(job_id)

    if _stage_status(job, "QC") == "SUCCEEDED":
        current = _current_passed_report(job_id)
        if current is None:
            raise RuntimeError("QC succeeded without a current passed QC report")
        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "job_status": "QC_PASSED",
            "qc_report_id": str(current["id"]),
            "qc_report_sha256": current["report_sha256"],
            "replayed": True,
            "publish_enabled": False,
            "production_execution_enabled": False,
        }

    start_provider_stage(job_id, "QC", actor=actor)
    try:
        report = analyze_qc(
            timeline,
            render,
            ffprobe_cli=ffprobe_cli,
            ffmpeg_cli=ffmpeg_cli,
            thresholds=thresholds,
        )
        report["render_manifest_sha256"] = render_manifest_sha
        report["lineage"] = {
            "animation_manifest_sha256": animation_sha,
            "render_manifest_sha256": render_manifest_sha,
            "render_artifact_sha256": render["artifact_sha256"],
            "props_sha256": render["props_sha256"],
            "project_source_sha256": render["project_source_sha256"],
            "composition_record_id": str(render["composition_record_id"]),
        }

        persisted = _persist_report(
            job_id,
            render=render,
            report=report,
            passed=bool(report["passed"]),
        )
        if not report["passed"]:
            raise QCRejected(report)

        written = write_provider_manifest(
            job_id,
            "QC",
            ProviderManifestEnvelope(
                manifest_kind="qc_report_manifest",
                schema_version="qc-report-v0.1",
                payload=report,
            ),
            actor=actor,
        )
        completed = complete_provider_stage(job_id, "QC", actor=actor)
        with engine.begin() as db:
            db.execute(
                text("""
                  UPDATE shrimp_animation_jobs
                  SET qc_report_sha256=:qc_sha,
                      updated_at=now()
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                """),
                {
                    "job_id": job_id,
                    "qc_sha": persisted["report_sha256"],
                },
            )

        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "job_status": completed["job_status"],
            "qc_manifest_sha256": written["content_sha256"],
            "qc_report_id": persisted["qc_report_id"],
            "qc_report_sha256": persisted["report_sha256"],
            "hard_failure_count": 0,
            "replayed": False,
            "next_stage": "PACKAGING_REVIEW",
            "publish_enabled": False,
            "production_execution_enabled": False,
        }
    except Exception as exc:
        fail_provider_stage(
            job_id,
            "QC",
            str(exc),
            retryable=False,
            actor=actor,
        )
        raise


def list_qc_reports(
    job_id,
    *,
    include_history: bool = False,
) -> list[dict]:
    sql = """
      SELECT id,render_id,render_artifact_sha256,
             animation_manifest_sha256,analyzer_version,
             report_sha256,report_content,passed,
             hard_failure_count,qc_status,created_at,superseded_at
      FROM shrimp_animation_qc_reports
      WHERE provider_job_id=CAST(:job_id AS uuid)
    """
    if not include_history:
        sql += " AND qc_status='PASSED'"
    sql += " ORDER BY created_at DESC"
    with engine.connect() as db:
        return [
            dict(row)
            for row in db.execute(
                text(sql),
                {"job_id": job_id},
            ).mappings().all()
        ]
