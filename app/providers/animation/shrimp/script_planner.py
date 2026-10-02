from app.providers.animation.models import (
    CharacterBrief,
    ContentBrief,
    DialogueLine,
    ScriptManifest,
    ScriptScene,
    StoryManifest,
    manifest_sha256,
)


_EMOTIONS = ("curious", "concerned", "confident", "surprised", "neutral")


def plan_script(
    brief: ContentBrief,
    story: StoryManifest,
) -> ScriptManifest:
    if not isinstance(brief, ContentBrief):
        brief = ContentBrief.model_validate(brief)
    if not isinstance(story, StoryManifest):
        story = StoryManifest.model_validate(story)
    if story.episode_id != brief.episode_id:
        raise ValueError("story and brief episode_id mismatch")
    if story.brief_sha256 != manifest_sha256(brief):
        raise ValueError("story does not match content brief")

    character_by_id: dict[str, CharacterBrief] = {
        character.character_id: character
        for character in brief.characters
    }
    scenes = []
    for index, beat in enumerate(story.beats, start=1):
        speakers = [
            character_by_id[character_id]
            for character_id in beat.featured_character_ids
            if character_id in character_by_id
        ]
        if len(speakers) < 2:
            raise ValueError("story beat requires at least two known characters")
        first, second = speakers[0], speakers[1]
        scenes.append(
            ScriptScene(
                scene_id=f"s{index:03d}",
                beat_id=beat.beat_id,
                summary=f"{beat.objective} {beat.conflict} {beat.turn}",
                dialogue=[
                    DialogueLine(
                        line_id=f"l{index:03d}-01",
                        speaker=first.character_id,
                        text=(
                            f"{first.display_name}: We need to move {brief.title} "
                            f"forward in scene {index}."
                        ),
                        emotion=_EMOTIONS[(index - 1) % len(_EMOTIONS)],
                        order=1,
                    ),
                    DialogueLine(
                        line_id=f"l{index:03d}-02",
                        speaker=second.character_id,
                        text=(
                            f"{second.display_name}: Then we handle the constraint "
                            "and make the next step explicit."
                        ),
                        emotion=_EMOTIONS[index % len(_EMOTIONS)],
                        order=2,
                    ),
                ],
            )
        )

    return ScriptManifest(
        episode_id=brief.episode_id,
        language=brief.language,
        story_sha256=manifest_sha256(story),
        scenes=scenes,
    )
