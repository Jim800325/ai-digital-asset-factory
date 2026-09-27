from app.clustering import semantic_tokens, similarity, cluster_key

def test_pricing_titles_cluster():
    a="AI pricing tracker"
    b="SaaS price monitor"
    c="competitor pricing database"
    assert "pricing" in semantic_tokens(a)
    assert "tracker" in semantic_tokens(b)
    assert similarity(a,b) >= 0.2
    assert similarity(a,c) >= 0.1

def test_utf8_normalization_is_safe():
    value="中文价格监控 数据库 — AI 工具"
    tokens=semantic_tokens(value)
    assert len(tokens) > 0
    key=cluster_key("DATASET_API",value)
    assert len(key.key)==64
    assert "中文价格监控" in key.canonical_title

def test_synonyms_are_normalized():
    assert "tracker" in semantic_tokens("price monitoring")
    assert "pricing" in semantic_tokens("prices tracker")
