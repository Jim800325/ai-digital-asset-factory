import hashlib
import re
import unicodedata
from dataclasses import dataclass

STOPWORDS = {
    "a","an","and","the","for","to","of","with","in","on","is","are","new","best",
    "open","source","free","ai","saas","software","app","online","platform"
}
SYNONYMS = {
    "monitor":"tracker","monitoring":"tracker","tracking":"tracker","track":"tracker",
    "prices":"pricing","price":"pricing","cost":"pricing","costs":"pricing",
    "competitors":"competitor","competitive":"competitor",
    "database":"data","dataset":"data","datasets":"data",
    "analytics":"analysis","analyzer":"analysis",
    "automation":"automate","automated":"automate",
    "reports":"report","reporting":"report",
    "apis":"api","tools":"tool"
}

@dataclass(frozen=True)
class ClusterMatch:
    key: str
    canonical_title: str
    tokens: tuple[str, ...]
    confidence: float

def normalize_text(value: str) -> str:
    value=unicodedata.normalize("NFKC", value or "").lower()
    value=re.sub(r"[^\w\s-]+"," ",value,flags=re.UNICODE)
    value=value.replace("_"," ").replace("-"," ")
    return re.sub(r"\s+"," ",value).strip()

def semantic_tokens(value: str) -> tuple[str, ...]:
    words=[]
    for raw in normalize_text(value).split():
        word=SYNONYMS.get(raw,raw)
        if len(word) < 2 or word in STOPWORDS:
            continue
        words.append(word)
    return tuple(sorted(set(words)))

def jaccard(a: tuple[str,...], b: tuple[str,...]) -> float:
    sa,sb=set(a),set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb)/len(sa | sb)

def cluster_key(asset_type: str, title: str) -> ClusterMatch:
    tokens=semantic_tokens(title)
    # Keep strongest concepts stable across title order and minor wording changes.
    concepts=tokens[:8]
    material=asset_type+"|"+"|".join(concepts)
    key=hashlib.sha256(material.encode("utf-8")).hexdigest()
    canonical=" ".join(concepts) if concepts else normalize_text(title)[:180]
    return ClusterMatch(key,canonical,concepts,1.0)

CONCEPT_GROUPS = [
    {"pricing"},
    {"tracker","data"},
    {"competitor"},
    {"report","analysis"},
    {"automate","workflow"},
    {"api"},
    {"tool"},
]

def similarity(title_a: str, title_b: str) -> float:
    a,b=semantic_tokens(title_a),semantic_tokens(title_b)
    base=jaccard(a,b)
    sa,sb=set(a),set(b)
    shared_groups=sum(1 for group in CONCEPT_GROUPS if sa & group and sb & group)
    # Shared high-value concepts can bridge different surface wording.
    bonus=min(0.35, shared_groups * 0.18)
    if "pricing" in sa and "pricing" in sb:
        bonus=max(bonus,0.30)
    return min(1.0, base+bonus)
