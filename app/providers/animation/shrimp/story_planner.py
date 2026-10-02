from app.providers.animation.models import (
    ContentBrief,
    StoryBeat,
    StoryManifest,
    manifest_sha256,
)


_PHASES = {
    2: ("SETUP", "RESOLUTION"),
    3: ("SETUP", "TURN", "RESOLUTION"),
}


def _phase_for(index: int, total: int) -> str:
    if total in _PHASES:
        return _PHASES[total][index]
    if index == 0:
        return "SETUP"
    if index == total - 1:
        return "RESOLUTION"
    if index == total // 2:
        return "TURN"
    return "ESCALATION"


def plan_story(brief: ContentBrief) -> StoryManifest:
    if not isinstance(brief, ContentBrief):
        brief = ContentBrief.model_validate(brief)
    brief_sha = manifest_sha256(brief)
    characters = brief.characters
    beats = []
    for index in range(brief.scene_count):
        primary = characters[index % len(characters)]
        secondary = characters[(index + 1) % len(characters)]
        phase = _phase_for(index, brief.scene_count)
        number = index + 1
        beats.append(
            StoryBeat(
                beat_id=f"b{number:03d}",
                scene_index=number,
                phase=phase,
                objective=(
                    f"{primary.display_name} advances the episode premise: "
                    f"{brief.premise[:260]}"
                ),
                conflict=(
                    f"{secondary.display_name} introduces a constraint that keeps "
                    f"scene {number} focused on {brief.title}."
                ),
                turn=(
                    f"Scene {number} ends with a {phase.lower()} beat that changes "
                    "what the characters must do next."
                ),
                featured_character_ids=[
                    primary.character_id,
                    secondary.character_id,
                ],
            )
        )

    return StoryManifest(
        episode_id=brief.episode_id,
        title=brief.title,
        language=brief.language,
        brief_sha256=brief_sha,
        logline=(
            f"{brief.title}: {brief.premise[:420]} "
            f"For {brief.audience}."
        ),
        beats=beats,
    )
