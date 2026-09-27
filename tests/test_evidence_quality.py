from app.evidence_quality import diversity_score, evidence_quality
from app.scoring import status_for

def test_candidate_gate():
    assert status_for(80, 2, evidence_quality=70, source_diversity=60, signal_strength=60) == "CANDIDATE"
    assert status_for(90, 2, evidence_quality=64, source_diversity=100, signal_strength=100) == "RESEARCH"
    assert status_for(90, 1, evidence_quality=100, source_diversity=100, signal_strength=100) == "RESEARCH"

def test_source_diversity():
    assert diversity_score(["web", "web"]) < 60
    assert diversity_score(["web", "code"]) >= 60

def test_quality_direction():
    assert evidence_quality([78, 82], [75, 80], 85) > evidence_quality([58, 58], [45, 45], 35)
