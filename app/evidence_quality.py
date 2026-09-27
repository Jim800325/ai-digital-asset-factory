from urllib.parse import urlparse

SOURCE_CLASS_WEIGHTS = {
    "community": 82.0,
    "code": 78.0,
    "product": 72.0,
    "news": 68.0,
    "web": 58.0,
    "unknown": 50.0,
}

COMMUNITY_DOMAINS = {"reddit.com", "news.ycombinator.com", "stackoverflow.com"}
CODE_DOMAINS = {"github.com", "gitlab.com"}
PRODUCT_DOMAINS = {"producthunt.com", "g2.com", "capterra.com"}

def source_class_for(source_type: str, url: str) -> str:
    domain=urlparse(url).netloc.lower().removeprefix("www.")
    st=(source_type or "").upper()
    if "GITHUB" in st or domain in CODE_DOMAINS:
        return "code"
    if domain in COMMUNITY_DOMAINS:
        return "community"
    if domain in PRODUCT_DOMAINS:
        return "product"
    if "NEWS" in st:
        return "news"
    if domain:
        return "web"
    return "unknown"

def evidence_metrics(*, source_type: str, url: str, hits: int, pain: int, confidence: float):
    source_class=source_class_for(source_type,url)
    source_quality=SOURCE_CLASS_WEIGHTS[source_class]
    signal_strength=min(100.0, 30.0 + hits*10.0 + pain*12.0 + confidence*15.0)
    return source_class, round(source_quality,2), round(signal_strength,2)

def diversity_score(source_classes) -> float:
    classes={c for c in source_classes if c and c!="unknown"}
    if not classes:
        return 0.0
    return round(min(100.0, 35.0 + len(classes)*25.0),2)

def evidence_quality(source_quality_values, signal_strength_values, diversity: float) -> float:
    if not source_quality_values:
        return 0.0
    avg_quality=sum(source_quality_values)/len(source_quality_values)
    avg_signal=sum(signal_strength_values)/len(signal_strength_values) if signal_strength_values else 0.0
    return round(min(100.0, avg_quality*0.40 + avg_signal*0.40 + diversity*0.20),2)
