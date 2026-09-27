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

def status_for(score: float, independent_sources: int) -> str:
    # v0.2: no candidate may graduate without independent evidence.
    if score >= 75 and independent_sources >= 2:
        return "CANDIDATE"
    if score >= 60:
        return "RESEARCH"
    return "WATCH"
