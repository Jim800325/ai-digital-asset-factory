from app.url_safety import _address_is_public, safe_url_syntax

def test_private_and_loopback_urls_are_blocked():
    assert not safe_url_syntax("http://127.0.0.1/admin")
    assert not safe_url_syntax("http://10.0.0.5/")
    assert not safe_url_syntax("http://192.168.1.10/")
    assert not safe_url_syntax("http://localhost:8000/")
    assert not safe_url_syntax("http://service.internal/")

def test_credentials_in_url_are_blocked():
    assert not safe_url_syntax("https://user:pass@example.com/")

def test_normal_public_url_syntax_is_allowed():
    assert safe_url_syntax("https://example.com/path?q=1")

def test_ip_classification():
    assert _address_is_public("8.8.8.8")
    assert not _address_is_public("127.0.0.1")
    assert not _address_is_public("169.254.169.254")
