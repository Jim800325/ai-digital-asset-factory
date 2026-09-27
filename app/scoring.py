ASSET_WEIGHTS = {
    "demand": 0.20,
    "repeatability": 0.20,
    "automation": 0.20,
    "ownership": 0.15,
    "margin": 0.15,
    "evidence": 0.10,
}

def score_asset(*, demand, repeatability, automation, ownership, margin, evidence):
    value = (
        demand * ASSET_WEIGHTS["demand"]
        + repeatability * ASSET_WEIGHTS["repeatability"]
        + automation * ASSET_WEIGHTS["automation"]
        + ownership * ASSET_WEIGHTS["ownership"]
        + margin * ASSET_WEIGHTS["margin"]
        + evidence * ASSET_WEIGHTS["evidence"]
    )
    return round(max(0, min(100, value)), 2)

def status_for(
    score: float,
    independent_sources: int,
    *,
    evidence_quality: float = 0.0,
    source_diversity: float = 0.0,
    signal_strength: float = 0.0,
) -> str:
    # v0.2 evidence gate: score alone can never graduate an opportunity.
    gate = (
        independent_sources >= 2
        and evidence_quality >= 65
        and source_diversity >= 60
        and signal_strength >= 55
    )
    if score >= 75 and gate:
        return "CANDIDATE"
    if score >= 60:
        return "RESEARCH"
    return "WATCH"
