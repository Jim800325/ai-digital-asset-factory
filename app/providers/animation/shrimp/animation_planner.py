from __future__ import annotations

from app.providers.animation.models import (
    AnimationSceneTimeline,
    AnimationTimelineManifest,
    AssetArtifactManifest,
    SceneManifest,
    SubtitleCue,
    TimelineAudioCue,
    TimelineCameraCue,
    TimelineCharacterCue,
    TimelineMediaRef,
    VoiceArtifactManifest,
)


ANIMATION_PLANNER_VERSION = "animation-planner-v0.1-deterministic"


def _ms_to_frames(ms: int, fps: int) -> int:
    return max(0, (int(ms) * int(fps) + 500) // 1000)


def _relative_frame(ms: int, fps: int, duration_frames: int) -> int:
    return min(duration_frames, _ms_to_frames(ms, fps))


def _media_ref(artifact) -> TimelineMediaRef:
    return TimelineMediaRef(
        logical_key=artifact.logical_key,
        storage_uri=artifact.storage_uri,
        media_type=artifact.media_type,
        sha256=artifact.sha256,
    )


def plan_animation_timeline(
    scene: SceneManifest,
    assets: AssetArtifactManifest,
    voices: VoiceArtifactManifest,
    *,
    scene_manifest_sha256: str,
    asset_manifest_sha256: str,
    voice_manifest_sha256: str,
) -> AnimationTimelineManifest:
    if scene.episode_id != assets.episode_id:
        raise ValueError("asset manifest episode_id does not match scene manifest")
    if scene.episode_id != voices.episode_id:
        raise ValueError("voice manifest episode_id does not match scene manifest")

    asset_map = {
        artifact.logical_key: artifact
        for artifact in assets.artifacts
    }
    voice_map = {
        artifact.logical_key: artifact
        for artifact in voices.artifacts
    }

    timeline_scenes: list[AnimationSceneTimeline] = []
    global_cursor = 0

    for render_scene in scene.scenes:
        duration_frames = max(
            1,
            _ms_to_frames(render_scene.duration_ms, scene.fps),
        )
        background_key = f"background:{render_scene.background_id}"
        background_artifact = asset_map.get(background_key)
        if background_artifact is None:
            raise ValueError(
                f"verified background artifact is missing: {background_key}"
            )

        camera = []
        for cue in render_scene.camera:
            start = _relative_frame(
                cue.start_ms,
                scene.fps,
                duration_frames,
            )
            end = _relative_frame(
                cue.end_ms,
                scene.fps,
                duration_frames,
            )
            if end <= start:
                end = min(duration_frames, start + 1)
            if end <= start:
                raise ValueError("camera cue cannot fit in scene duration")
            camera.append(
                TimelineCameraCue(
                    type=cue.type,
                    start_frame=start,
                    end_frame=end,
                    from_scale=cue.from_scale,
                    to_scale=cue.to_scale,
                )
            )

        characters = []
        for action in render_scene.characters:
            logical_key = (
                f"character:{action.character_id}:{action.asset_variant}"
            )
            artifact = asset_map.get(logical_key)
            if artifact is None:
                raise ValueError(
                    f"verified character artifact is missing: {logical_key}"
                )
            start = _relative_frame(
                action.start_ms,
                scene.fps,
                duration_frames,
            )
            end = _relative_frame(
                action.end_ms,
                scene.fps,
                duration_frames,
            )
            if end <= start:
                end = min(duration_frames, start + 1)
            if end <= start:
                raise ValueError("character cue cannot fit in scene duration")
            characters.append(
                TimelineCharacterCue(
                    character_id=action.character_id,
                    asset=_media_ref(artifact),
                    x=action.x,
                    y=action.y,
                    action=action.action,
                    start_frame=start,
                    end_frame=end,
                )
            )

        audio = []
        subtitles = []
        for line in render_scene.dialogue:
            artifact = voice_map.get(line.voice_asset_id)
            if artifact is None:
                raise ValueError(
                    f"verified voice artifact is missing: {line.voice_asset_id}"
                )
            if artifact.duration_ms is None:
                raise ValueError("voice artifact has no measured duration")

            start = _relative_frame(
                line.start_ms,
                scene.fps,
                duration_frames,
            )
            planned_end = _relative_frame(
                line.end_ms,
                scene.fps,
                duration_frames,
            )
            measured_frames = max(
                1,
                _ms_to_frames(artifact.duration_ms, scene.fps),
            )
            measured_end = start + measured_frames
            end = max(planned_end, measured_end)
            if end > duration_frames:
                raise ValueError(
                    f"voice artifact overflows scene {render_scene.scene_id}: "
                    f"{line.voice_asset_id}"
                )
            if end <= start:
                raise ValueError("dialogue cue cannot fit in scene duration")

            audio.append(
                TimelineAudioCue(
                    voice_asset_id=line.voice_asset_id,
                    line_id=line.line_id,
                    speaker=line.speaker,
                    media=_media_ref(artifact),
                    start_frame=start,
                    end_frame=end,
                    measured_duration_ms=artifact.duration_ms,
                )
            )
            subtitles.append(
                SubtitleCue(
                    line_id=line.line_id,
                    speaker=line.speaker,
                    text=line.text,
                    start_frame=start,
                    end_frame=end,
                )
            )

        timeline_scenes.append(
            AnimationSceneTimeline(
                scene_id=render_scene.scene_id,
                start_frame=global_cursor,
                end_frame=global_cursor + duration_frames,
                duration_frames=duration_frames,
                background=_media_ref(background_artifact),
                transition=render_scene.transition,
                camera=camera,
                characters=characters,
                audio=audio,
                subtitles=subtitles,
            )
        )
        global_cursor += duration_frames

    return AnimationTimelineManifest(
        episode_id=scene.episode_id,
        fps=scene.fps,
        resolution=scene.resolution,
        scene_manifest_sha256=scene_manifest_sha256,
        asset_manifest_sha256=asset_manifest_sha256,
        voice_manifest_sha256=voice_manifest_sha256,
        total_duration_frames=global_cursor,
        scenes=timeline_scenes,
    )
