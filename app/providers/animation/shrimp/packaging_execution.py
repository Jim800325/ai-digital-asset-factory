from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
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
    retry_provider_stage,
    start_provider_stage,
    write_provider_manifest,
)
from app.providers.animation.models import AnimationTimelineManifest, canonical_json


PACKAGING_VERSION = "episode-package-v0.1-deterministic"
REVIEW_GENERATOR_VERSION = "shrimp-review-v0.1-deterministic"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _stage_status(job: dict, key: str) -> str:
    return next(
        stage["stage_status"]
        for stage in job["stages"]
        if stage["stage_key"] == key
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise PermissionError("Episode packaging requires a local file URI")
    if parsed.netloc not in ("", "localhost"):
        raise PermissionError("Remote file URI hosts are not allowed")
    return Path(url2pathname(unquote(parsed.path))).resolve()


def _canonical_bytes(value) -> bytes:
    return canonical_json(value).encode("utf-8")


def _current_manifest(job_id, stage_key: str, manifest_kind: str) -> tuple[dict, str]:
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


def _all_current_manifest_hashes(job_id) -> list[dict]:
    with engine.connect() as db:
        rows = db.execute(
            text("""
              SELECT stage_key,manifest_kind,schema_version,
                     manifest_version,content_sha256
              FROM production_provider_manifests
              WHERE job_id=CAST(:job_id AS uuid)
                AND is_current=true
              ORDER BY stage_key,manifest_kind,manifest_version
            """),
            {"job_id": job_id},
        ).mappings().all()
    return [
        {
            "stage_key": row["stage_key"],
            "manifest_kind": row["manifest_kind"],
            "schema_version": row["schema_version"],
            "manifest_version": int(row["manifest_version"]),
            "content_sha256": row["content_sha256"],
        }
        for row in rows
    ]


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
        raise RuntimeError("Current render artifact is missing")
    return dict(row)


def _current_qc(job_id) -> dict:
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
    if row is None:
        raise RuntimeError("Current passed QC report is missing")
    return dict(row)


def _current_bundle(job_id) -> dict | None:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_episode_bundles
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND bundle_status='CURRENT'
              ORDER BY created_at DESC
              LIMIT 1
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
    return dict(row) if row else None


def _current_review(job_id) -> dict | None:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT *
              FROM shrimp_animation_release_review_packages
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND review_status='READY_FOR_HUMAN_REVIEW'
              ORDER BY created_at DESC
              LIMIT 1
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
    return dict(row) if row else None


def _subtitle_srt(timeline: AnimationTimelineManifest) -> str:
    rows = []
    for scene in timeline.scenes:
        for cue in scene.subtitles:
            rows.append(
                (
                    scene.start_frame + cue.start_frame,
                    scene.start_frame + cue.end_frame,
                    cue.speaker,
                    cue.text,
                )
            )
    rows.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    def stamp(frame: int) -> str:
        ms = (int(frame) * 1000 + timeline.fps // 2) // timeline.fps
        hours, rest = divmod(ms, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        seconds, millis = divmod(rest, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    blocks = []
    for index, (start, end, speaker, line) in enumerate(rows, 1):
        blocks.append(
            f"{index}\n{stamp(start)} --> {stamp(end)}\n{speaker}: {line}\n"
        )
    return "\n".join(blocks)


def _transcript(timeline: AnimationTimelineManifest) -> list[dict]:
    rows = []
    for scene in timeline.scenes:
        for cue in scene.subtitles:
            rows.append(
                {
                    "scene_id": scene.scene_id,
                    "line_id": cue.line_id,
                    "speaker": cue.speaker,
                    "text": cue.text,
                    "start_frame": scene.start_frame + cue.start_frame,
                    "end_frame": scene.start_frame + cue.end_frame,
                }
            )
    return sorted(
        rows,
        key=lambda item: (
            item["start_frame"],
            item["end_frame"],
            item["line_id"],
        ),
    )


def _provenance_summary(manifest: dict) -> dict:
    artifacts = list(manifest.get("artifacts") or [])
    return {
        "artifact_count": len(artifacts),
        "all_usage_rights_approved": all(
            item.get("usage_rights") == "APPROVED"
            for item in artifacts
        ),
        "items": [
            {
                "logical_key": item.get("logical_key"),
                "artifact_kind": item.get("artifact_kind"),
                "source_mode": item.get("source_mode"),
                "media_type": item.get("media_type"),
                "byte_size": item.get("byte_size"),
                "sha256": item.get("sha256"),
                "license_id": item.get("license_id"),
                "usage_rights": item.get("usage_rights"),
                "duration_ms": item.get("duration_ms"),
                "provenance": item.get("provenance"),
            }
            for item in sorted(
                artifacts,
                key=lambda value: str(value.get("logical_key") or ""),
            )
        ],
    }


def _entry(name: str, data: bytes, media_type: str) -> dict:
    return {
        "relative_path": name,
        "sha256": _sha256_bytes(data),
        "byte_size": len(data),
        "media_type": media_type,
    }


def _episode_entry(path: Path, render: dict) -> dict:
    sha = _sha256_file(path)
    size = path.stat().st_size
    if sha != render["artifact_sha256"]:
        raise RuntimeError("Rendered MP4 bytes drifted before packaging")
    if size != int(render["byte_size"]):
        raise RuntimeError("Rendered MP4 size drifted before packaging")
    return {
        "relative_path": "episode.mp4",
        "sha256": sha,
        "byte_size": size,
        "media_type": "video/mp4",
    }


def _write_zip_entry(
    archive: zipfile.ZipFile,
    name: str,
    source: bytes | Path,
) -> None:
    info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    if isinstance(source, bytes):
        archive.writestr(info, source)
        return
    with source.open("rb") as src, archive.open(info, "w") as dest:
        shutil.copyfileobj(src, dest, length=1024 * 1024)


def _build_bundle(
    output_root: Path,
    timeline: AnimationTimelineManifest,
    render: dict,
    assets: dict,
    voices: dict,
    qc: dict,
    manifest_hashes: list[dict],
) -> dict:
    output_root.mkdir(parents=True, exist_ok=True)
    render_path = _file_path(render["artifact_uri"])
    if not render_path.is_file():
        raise RuntimeError("Rendered MP4 is missing before packaging")

    timeline_bytes = _canonical_bytes(timeline.model_dump(mode="json"))
    subtitle_bytes = _subtitle_srt(timeline).encode("utf-8")
    asset_bytes = _canonical_bytes(
        {
            "schema_version": "artifact-provenance-v0.1",
            "kind": "ASSETS",
            "episode_id": timeline.episode_id,
            "provenance": _provenance_summary(assets),
        }
    )
    voice_bytes = _canonical_bytes(
        {
            "schema_version": "artifact-provenance-v0.1",
            "kind": "VOICES",
            "episode_id": timeline.episode_id,
            "provenance": _provenance_summary(voices),
        }
    )
    qc_bytes = _canonical_bytes(
        {
            **qc["report_content"],
            "report_sha256": qc["report_sha256"],
        }
    )
    hashes_bytes = _canonical_bytes(
        {
            "schema_version": "manifest-hashes-v0.1",
            "episode_id": timeline.episode_id,
            "manifests": manifest_hashes,
            "render_artifact_sha256": render["artifact_sha256"],
            "qc_report_sha256": qc["report_sha256"],
        }
    )

    payloads = [
        (
            "episode.mp4",
            render_path,
            "video/mp4",
            _episode_entry(render_path, render),
        ),
        (
            "timeline.json",
            timeline_bytes,
            "application/json",
            _entry("timeline.json", timeline_bytes, "application/json"),
        ),
        (
            "subtitles.srt",
            subtitle_bytes,
            "application/x-subrip",
            _entry("subtitles.srt", subtitle_bytes, "application/x-subrip"),
        ),
        (
            "asset_provenance.json",
            asset_bytes,
            "application/json",
            _entry("asset_provenance.json", asset_bytes, "application/json"),
        ),
        (
            "voice_provenance.json",
            voice_bytes,
            "application/json",
            _entry("voice_provenance.json", voice_bytes, "application/json"),
        ),
        (
            "qc_report.json",
            qc_bytes,
            "application/json",
            _entry("qc_report.json", qc_bytes, "application/json"),
        ),
        (
            "manifest_hashes.json",
            hashes_bytes,
            "application/json",
            _entry("manifest_hashes.json", hashes_bytes, "application/json"),
        ),
    ]
    artifact_manifest = [
        item[3]
        for item in sorted(payloads, key=lambda value: value[0])
    ]
    bundle_manifest = {
        "schema_version": "episode-bundle-manifest-v0.1",
        "packaging_version": PACKAGING_VERSION,
        "episode_id": timeline.episode_id,
        "artifacts": artifact_manifest,
    }
    bundle_manifest_bytes = _canonical_bytes(bundle_manifest)
    bundle_manifest_sha = _sha256_bytes(bundle_manifest_bytes)

    final_path = (
        output_root
        / f"{timeline.episode_id}-{qc['report_sha256'][:16]}.zip"
    ).resolve()
    if output_root.resolve() not in final_path.parents:
        raise PermissionError("Episode bundle path escapes output root")

    with tempfile.NamedTemporaryFile(
        prefix="shrimp-bundle-",
        suffix=".zip",
        dir=output_root,
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
    try:
        with zipfile.ZipFile(
            temp_path,
            mode="w",
            compression=zipfile.ZIP_STORED,
            strict_timestamps=True,
        ) as archive:
            for name, source, _, _ in sorted(
                payloads,
                key=lambda item: item[0],
            ):
                _write_zip_entry(archive, name, source)
            _write_zip_entry(
                archive,
                "bundle_manifest.json",
                bundle_manifest_bytes,
            )
        temp_path.replace(final_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "bundle_uri": final_path.as_uri(),
        "bundle_sha256": _sha256_file(final_path),
        "byte_size": final_path.stat().st_size,
        "artifact_manifest": artifact_manifest,
        "bundle_manifest": bundle_manifest,
        "bundle_manifest_sha256": bundle_manifest_sha,
    }


def _review_content(
    brief: dict,
    timeline: AnimationTimelineManifest,
    render: dict,
    qc: dict,
    assets: dict,
    voices: dict,
    bundle: dict,
    manifest_hashes: list[dict],
) -> dict:
    duration_ms = (
        timeline.total_duration_frames * 1000 + timeline.fps // 2
    ) // timeline.fps
    return {
        "schema_version": "shrimp-review-package-v0.1",
        "generator_version": REVIEW_GENERATOR_VERSION,
        "review_status": "READY_FOR_HUMAN_REVIEW",
        "episode": {
            "episode_id": timeline.episode_id,
            "title": brief["title"],
            "language": brief["language"],
            "duration_ms": duration_ms,
            "scene_count": len(timeline.scenes),
            "width": timeline.resolution.width,
            "height": timeline.resolution.height,
            "fps": timeline.fps,
            "frame_count": timeline.total_duration_frames,
        },
        "bundle": {
            "filename": _file_path(bundle["bundle_uri"]).name,
            "sha256": bundle["bundle_sha256"],
            "byte_size": bundle["byte_size"],
            "bundle_manifest_sha256": bundle["bundle_manifest_sha256"],
            "artifacts": bundle["artifact_manifest"],
        },
        "qc": {
            "report_sha256": qc["report_sha256"],
            "passed": bool(qc["passed"]),
            "hard_failure_count": int(qc["hard_failure_count"]),
            "checks": qc["report_content"]["checks"],
        },
        "media": {
            "render_artifact_sha256": render["artifact_sha256"],
            "width": int(render["width"]),
            "height": int(render["height"]),
            "fps": float(render["fps"]),
            "frame_count": int(render["frame_count"]),
            "duration_ms": int(render["duration_ms"]),
        },
        "provenance": {
            "assets": _provenance_summary(assets),
            "voices": _provenance_summary(voices),
        },
        "manifest_hashes": manifest_hashes,
        "transcript": _transcript(timeline),
        "reviewer_checklist": [
            {
                "key": "watch_full_episode",
                "label": "Watch the complete episode from start to finish",
                "required": True,
            },
            {
                "key": "verify_dialogue_and_subtitles",
                "label": "Verify dialogue, subtitle text, and timing",
                "required": True,
            },
            {
                "key": "verify_visual_continuity",
                "label": "Verify visual continuity and character consistency",
                "required": True,
            },
            {
                "key": "verify_rights_and_provenance",
                "label": "Verify asset and voice rights and provenance",
                "required": True,
            },
            {
                "key": "approve_release_intent",
                "label": "Explicitly approve any later release action",
                "required": True,
            },
        ],
        "safety": {
            "human_review_required": True,
            "auto_publish": False,
            "production_execution_enabled": False,
            "external_side_effects": "DENY",
        },
    }


def _review_markdown(content: dict) -> str:
    episode = content["episode"]
    bundle = content["bundle"]
    qc = content["qc"]
    provenance = content["provenance"]
    lines = [
        f"# Episode Review - {episode['title']}",
        "",
        f"- Episode ID: {episode['episode_id']}",
        f"- Language: {episode['language']}",
        f"- Duration: {episode['duration_ms']} ms",
        f"- Resolution: {episode['width']}x{episode['height']}",
        f"- FPS: {episode['fps']}",
        f"- Bundle SHA-256: {bundle['sha256']}",
        f"- QC report SHA-256: {qc['report_sha256']}",
        "",
        "## QC",
        "",
        f"- Passed: {str(qc['passed']).lower()}",
        f"- Hard failures: {qc['hard_failure_count']}",
    ]
    for check in qc["checks"]:
        state = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- [{state}] {check['key']}")

    lines.extend(
        [
            "",
            "## Provenance",
            "",
            (
                f"- Asset artifacts: {provenance['assets']['artifact_count']} "
                f"(rights approved: "
                f"{str(provenance['assets']['all_usage_rights_approved']).lower()})"
            ),
            (
                f"- Voice artifacts: {provenance['voices']['artifact_count']} "
                f"(rights approved: "
                f"{str(provenance['voices']['all_usage_rights_approved']).lower()})"
            ),
            "",
            "## Transcript",
            "",
        ]
    )
    for row in content["transcript"]:
        lines.append(
            f"- {row['scene_id']} {row['line_id']} "
            f"{row['speaker']}: {row['text']}"
        )

    lines.extend(["", "## Human Review Checklist", ""])
    for item in content["reviewer_checklist"]:
        lines.append(f"- [ ] {item['label']} ({item['key']})")

    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Automatic publish: false",
            "- Production execution: false",
            "- Human review required: true",
            "",
        ]
    )
    return "\n".join(lines)


def _build_review(
    output_root: Path,
    brief: dict,
    timeline: AnimationTimelineManifest,
    render: dict,
    qc: dict,
    assets: dict,
    voices: dict,
    bundle: dict,
    manifest_hashes: list[dict],
) -> dict:
    content = _review_content(
        brief,
        timeline,
        render,
        qc,
        assets,
        voices,
        bundle,
        manifest_hashes,
    )
    package_sha = _sha256_bytes(_canonical_bytes(content))
    document_bytes = _review_markdown(content).encode("utf-8")
    document_path = (
        output_root
        / f"{timeline.episode_id}-review-{package_sha[:16]}.md"
    ).resolve()
    if output_root.resolve() not in document_path.parents:
        raise PermissionError("Review document path escapes output root")

    with tempfile.NamedTemporaryFile(
        prefix="shrimp-review-",
        suffix=".md",
        dir=output_root,
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(document_bytes)
    try:
        temp_path.replace(document_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "package_content": content,
        "package_sha256": package_sha,
        "review_document_uri": document_path.as_uri(),
        "review_document_sha256": _sha256_file(document_path),
    }


def _persist_bundle(
    job_id,
    render: dict,
    qc: dict,
    timeline: AnimationTimelineManifest,
    animation_sha: str,
    assets_sha: str,
    voices_sha: str,
    bundle: dict,
) -> dict:
    with engine.begin() as db:
        current = db.execute(
            text("""
              SELECT id,qc_report_sha256,bundle_sha256
              FROM shrimp_animation_episode_bundles
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND bundle_status='CURRENT'
              FOR UPDATE
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
        if (
            current
            and current["qc_report_sha256"] == qc["report_sha256"]
            and current["bundle_sha256"] == bundle["bundle_sha256"]
        ):
            return {"bundle_id": str(current["id"]), "changed": False}
        if current:
            db.execute(
                text("""
                  UPDATE shrimp_animation_episode_bundles
                  SET bundle_status='STALE',
                      superseded_at=COALESCE(superseded_at,now())
                  WHERE id=:id
                """),
                {"id": current["id"]},
            )

        bundle_id = db.execute(
            text("""
              INSERT INTO shrimp_animation_episode_bundles(
                provider_job_id,render_id,qc_report_id,episode_id,
                packaging_version,animation_manifest_sha256,
                assets_manifest_sha256,voices_manifest_sha256,
                render_artifact_sha256,qc_report_sha256,
                bundle_manifest_sha256,bundle_uri,bundle_sha256,
                byte_size,artifact_manifest,bundle_manifest,bundle_status)
              VALUES(
                CAST(:job_id AS uuid),CAST(:render_id AS uuid),
                CAST(:qc_report_id AS uuid),:episode_id,
                :packaging_version,:animation_sha,:assets_sha,:voices_sha,
                :render_sha,:qc_sha,:bundle_manifest_sha,:bundle_uri,
                :bundle_sha,:byte_size,CAST(:artifact_manifest AS jsonb),
                CAST(:bundle_manifest AS jsonb),'CURRENT')
              RETURNING id
            """),
            {
                "job_id": job_id,
                "render_id": render["id"],
                "qc_report_id": qc["id"],
                "episode_id": timeline.episode_id,
                "packaging_version": PACKAGING_VERSION,
                "animation_sha": animation_sha,
                "assets_sha": assets_sha,
                "voices_sha": voices_sha,
                "render_sha": render["artifact_sha256"],
                "qc_sha": qc["report_sha256"],
                "bundle_manifest_sha": bundle["bundle_manifest_sha256"],
                "bundle_uri": bundle["bundle_uri"],
                "bundle_sha": bundle["bundle_sha256"],
                "byte_size": bundle["byte_size"],
                "artifact_manifest": canonical_json(bundle["artifact_manifest"]),
                "bundle_manifest": canonical_json(bundle["bundle_manifest"]),
            },
        ).scalar_one()
    return {"bundle_id": str(bundle_id), "changed": True}


def _persist_review(job_id, bundle_id: str, review: dict) -> dict:
    with engine.begin() as db:
        current = db.execute(
            text("""
              SELECT id,bundle_id,package_sha256
              FROM shrimp_animation_release_review_packages
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND review_status='READY_FOR_HUMAN_REVIEW'
              FOR UPDATE
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
        if (
            current
            and str(current["bundle_id"]) == str(bundle_id)
            and current["package_sha256"] == review["package_sha256"]
        ):
            return {"review_package_id": str(current["id"]), "changed": False}
        if current:
            db.execute(
                text("""
                  UPDATE shrimp_animation_release_review_packages
                  SET review_status='STALE',
                      superseded_at=COALESCE(superseded_at,now())
                  WHERE id=:id
                """),
                {"id": current["id"]},
            )

        review_id = db.execute(
            text("""
              INSERT INTO shrimp_animation_release_review_packages(
                provider_job_id,bundle_id,generator_version,review_status,
                package_sha256,package_content,review_document_uri,
                review_document_sha256)
              VALUES(
                CAST(:job_id AS uuid),CAST(:bundle_id AS uuid),
                :generator_version,'READY_FOR_HUMAN_REVIEW',
                :package_sha,CAST(:package_content AS jsonb),
                :document_uri,:document_sha)
              RETURNING id
            """),
            {
                "job_id": job_id,
                "bundle_id": bundle_id,
                "generator_version": REVIEW_GENERATOR_VERSION,
                "package_sha": review["package_sha256"],
                "package_content": canonical_json(review["package_content"]),
                "document_uri": review["review_document_uri"],
                "document_sha": review["review_document_sha256"],
            },
        ).scalar_one()
    return {"review_package_id": str(review_id), "changed": True}


def _validate_inputs(job_id) -> dict:
    job = get_provider_job(job_id)
    if _stage_status(job, "QC") != "SUCCEEDED":
        raise RuntimeError("QC must be SUCCEEDED before PACKAGE")

    brief, brief_sha = _current_manifest(
        job_id, "CONTENT_BRIEF", "content_brief"
    )
    assets, assets_sha = _current_manifest(
        job_id, "ASSETS", "asset_artifact_manifest"
    )
    voices, voices_sha = _current_manifest(
        job_id, "VOICES", "voice_artifact_manifest"
    )
    animation, animation_sha = _current_manifest(
        job_id, "ANIMATION", "animation_timeline_manifest"
    )
    render_manifest, render_manifest_sha = _current_manifest(
        job_id, "RENDER", "render_artifact_manifest"
    )
    qc_manifest, qc_manifest_sha = _current_manifest(
        job_id, "QC", "qc_report_manifest"
    )
    timeline = AnimationTimelineManifest.model_validate(animation["timeline"])
    render = _current_render(job_id)
    qc = _current_qc(job_id)

    if not qc["passed"] or int(qc["hard_failure_count"]) != 0:
        raise RuntimeError("PACKAGE requires zero-failure passed QC")
    if qc["render_artifact_sha256"] != render["artifact_sha256"]:
        raise RuntimeError("QC/render artifact lineage drift detected")
    if qc["animation_manifest_sha256"] != animation_sha:
        raise RuntimeError("QC/animation manifest lineage drift detected")
    if qc_manifest.get("render_artifact_sha256") != render["artifact_sha256"]:
        raise RuntimeError("QC manifest render hash drift detected")
    if qc_manifest.get("animation_manifest_sha256") != animation_sha:
        raise RuntimeError("QC manifest animation hash drift detected")
    if animation["timeline"]["asset_manifest_sha256"] != assets_sha:
        raise RuntimeError("Animation/assets manifest hash drift detected")
    if animation["timeline"]["voice_manifest_sha256"] != voices_sha:
        raise RuntimeError("Animation/voices manifest hash drift detected")
    if render_manifest["artifact"]["sha256"] != render["artifact_sha256"]:
        raise RuntimeError("Render manifest artifact hash drift detected")

    render_path = _file_path(render["artifact_uri"])
    if not render_path.is_file():
        raise RuntimeError("Rendered MP4 is missing before PACKAGE")
    if _sha256_file(render_path) != render["artifact_sha256"]:
        raise RuntimeError("Rendered MP4 hash drift detected before PACKAGE")

    with engine.connect() as db:
        meta = db.execute(
            text("""
              SELECT brief_sha256,assets_manifest_sha256,
                     voices_manifest_sha256,animation_manifest_sha256,
                     render_artifact_sha256,qc_report_sha256
              FROM shrimp_animation_jobs
              WHERE provider_job_id=CAST(:job_id AS uuid)
            """),
            {"job_id": job_id},
        ).mappings().one()

    expected = {
        "brief_sha256": brief_sha,
        "assets_manifest_sha256": assets_sha,
        "voices_manifest_sha256": voices_sha,
        "animation_manifest_sha256": animation_sha,
        "render_artifact_sha256": render["artifact_sha256"],
        "qc_report_sha256": qc["report_sha256"],
    }
    for key, value in expected.items():
        if meta[key] != value:
            raise RuntimeError(f"PACKAGE job metadata drift detected: {key}")

    return {
        "job": job,
        "brief": brief,
        "assets": assets,
        "voices": voices,
        "timeline": timeline,
        "animation_sha": animation_sha,
        "assets_sha": assets_sha,
        "voices_sha": voices_sha,
        "render": render,
        "render_manifest_sha": render_manifest_sha,
        "qc": qc,
        "qc_manifest_sha": qc_manifest_sha,
        "manifest_hashes": _all_current_manifest_hashes(job_id),
    }


def _verify_replay_files(bundle: dict, review: dict) -> None:
    bundle_path = _file_path(bundle["bundle_uri"])
    if not bundle_path.is_file():
        raise RuntimeError("Current episode bundle file is missing")
    if _sha256_file(bundle_path) != bundle["bundle_sha256"]:
        raise RuntimeError("Current episode bundle bytes drift detected")

    review_path = _file_path(review["review_document_uri"])
    if not review_path.is_file():
        raise RuntimeError("Current review document is missing")
    if _sha256_file(review_path) != review["review_document_sha256"]:
        raise RuntimeError("Current review document bytes drift detected")


def execute_package_stage(
    job_id,
    *,
    output_root: str,
    actor: str = "shrimp-package-worker",
) -> dict:
    if not str(output_root or "").strip():
        raise ValueError("PACKAGE output_root is required")
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    inputs = _validate_inputs(job_id)
    state = _stage_status(inputs["job"], "PACKAGE")

    if state == "SUCCEEDED":
        bundle = _current_bundle(job_id)
        review = _current_review(job_id)
        if bundle is None or review is None:
            raise RuntimeError(
                "PACKAGE succeeded without current bundle and review package"
            )
        _verify_replay_files(bundle, review)
        with engine.begin() as db:
            current_review_status = db.execute(
                text("""
                  SELECT review_status
                  FROM shrimp_animation_jobs
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                  FOR UPDATE
                """),
                {"job_id": job_id},
            ).scalar_one()
            target_review_status = (
                current_review_status
                if current_review_status in {
                    "RELEASE_APPROVED",
                    "RELEASE_REJECTED",
                }
                else "READY_FOR_HUMAN_REVIEW"
            )
            db.execute(
                text("""
                  UPDATE shrimp_animation_jobs
                  SET episode_bundle_sha256=:bundle_sha,
                      release_review_package_sha256=:review_sha,
                      review_status=:review_status,
                      updated_at=now()
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                """),
                {
                    "job_id": job_id,
                    "bundle_sha": bundle["bundle_sha256"],
                    "review_sha": review["package_sha256"],
                    "review_status": target_review_status,
                },
            )
        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "job_status": "QC_PASSED",
            "review_status": target_review_status,
            "bundle_id": str(bundle["id"]),
            "episode_bundle_sha256": bundle["bundle_sha256"],
            "review_package_id": str(review["id"]),
            "release_review_package_sha256": review["package_sha256"],
            "replayed": True,
            "publish_enabled": False,
            "production_execution_enabled": False,
        }

    if state == "WAITING_RETRY":
        retry_provider_stage(job_id, "PACKAGE", actor=actor)
        state = "PENDING"
    if state not in {"PENDING", "STALE"}:
        raise RuntimeError(f"PACKAGE cannot execute from {state}")

    start_provider_stage(job_id, "PACKAGE", actor=actor)
    try:
        bundle = _build_bundle(
            root,
            inputs["timeline"],
            inputs["render"],
            inputs["assets"],
            inputs["voices"],
            inputs["qc"],
            inputs["manifest_hashes"],
        )
        persisted_bundle = _persist_bundle(
            job_id,
            inputs["render"],
            inputs["qc"],
            inputs["timeline"],
            inputs["animation_sha"],
            inputs["assets_sha"],
            inputs["voices_sha"],
            bundle,
        )
        review = _build_review(
            root,
            inputs["brief"],
            inputs["timeline"],
            inputs["render"],
            inputs["qc"],
            inputs["assets"],
            inputs["voices"],
            bundle,
            inputs["manifest_hashes"],
        )
        persisted_review = _persist_review(
            job_id,
            persisted_bundle["bundle_id"],
            review,
        )

        package_manifest = {
            "schema_version": "episode-package-manifest-v0.1",
            "packaging_version": PACKAGING_VERSION,
            "episode_id": inputs["timeline"].episode_id,
            "episode_bundle": {
                "bundle_id": persisted_bundle["bundle_id"],
                "uri": bundle["bundle_uri"],
                "sha256": bundle["bundle_sha256"],
                "byte_size": bundle["byte_size"],
                "bundle_manifest_sha256": bundle[
                    "bundle_manifest_sha256"
                ],
                "artifact_manifest": bundle["artifact_manifest"],
            },
            "release_review_package": {
                "review_package_id": persisted_review[
                    "review_package_id"
                ],
                "review_status": "READY_FOR_HUMAN_REVIEW",
                "package_sha256": review["package_sha256"],
                "review_document_uri": review["review_document_uri"],
                "review_document_sha256": review[
                    "review_document_sha256"
                ],
            },
            "lineage": {
                "animation_manifest_sha256": inputs["animation_sha"],
                "assets_manifest_sha256": inputs["assets_sha"],
                "voices_manifest_sha256": inputs["voices_sha"],
                "render_manifest_sha256": inputs["render_manifest_sha"],
                "render_artifact_sha256": inputs["render"][
                    "artifact_sha256"
                ],
                "qc_manifest_sha256": inputs["qc_manifest_sha"],
                "qc_report_sha256": inputs["qc"]["report_sha256"],
            },
            "safety": {
                "human_review_required": True,
                "auto_publish": False,
                "production_execution_enabled": False,
                "external_side_effects": "DENY",
            },
        }
        written = write_provider_manifest(
            job_id,
            "PACKAGE",
            ProviderManifestEnvelope(
                manifest_kind="episode_package_manifest",
                schema_version="episode-package-v0.1",
                payload=package_manifest,
            ),
            actor=actor,
        )
        completed = complete_provider_stage(
            job_id,
            "PACKAGE",
            actor=actor,
        )
        with engine.begin() as db:
            db.execute(
                text("""
                  UPDATE shrimp_animation_jobs
                  SET episode_bundle_sha256=:bundle_sha,
                      release_review_package_sha256=:review_sha,
                      review_status='READY_FOR_HUMAN_REVIEW',
                      updated_at=now()
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                """),
                {
                    "job_id": job_id,
                    "bundle_sha": bundle["bundle_sha256"],
                    "review_sha": review["package_sha256"],
                },
            )

        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "job_status": completed["job_status"],
            "package_manifest_sha256": written["content_sha256"],
            "review_status": "READY_FOR_HUMAN_REVIEW",
            "bundle_id": persisted_bundle["bundle_id"],
            "episode_bundle_uri": bundle["bundle_uri"],
            "episode_bundle_sha256": bundle["bundle_sha256"],
            "bundle_artifact_count": len(bundle["artifact_manifest"]),
            "review_package_id": persisted_review["review_package_id"],
            "release_review_package_sha256": review["package_sha256"],
            "review_document_uri": review["review_document_uri"],
            "review_document_sha256": review[
                "review_document_sha256"
            ],
            "replayed": False,
            "next_stage": "HUMAN_REVIEW",
            "publish_enabled": False,
            "production_execution_enabled": False,
        }
    except Exception as exc:
        fail_provider_stage(
            job_id,
            "PACKAGE",
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


def list_episode_bundles(
    job_id,
    *,
    include_stale: bool = False,
) -> list[dict]:
    sql = """
      SELECT *
      FROM shrimp_animation_episode_bundles
      WHERE provider_job_id=CAST(:job_id AS uuid)
    """
    if not include_stale:
        sql += " AND bundle_status='CURRENT'"
    sql += " ORDER BY created_at DESC"
    with engine.connect() as db:
        return [
            dict(row)
            for row in db.execute(text(sql), {"job_id": job_id}).mappings()
        ]


def list_release_review_packages(
    job_id,
    *,
    include_stale: bool = False,
) -> list[dict]:
    sql = """
      SELECT *
      FROM shrimp_animation_release_review_packages
      WHERE provider_job_id=CAST(:job_id AS uuid)
    """
    if not include_stale:
        sql += " AND review_status='READY_FOR_HUMAN_REVIEW'"
    sql += " ORDER BY created_at DESC"
    with engine.connect() as db:
        return [
            dict(row)
            for row in db.execute(text(sql), {"job_id": job_id}).mappings()
        ]
