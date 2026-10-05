from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from app.providers.animation.shrimp import bilibili_signing_provider as provider


def _public_b64(key: Ed25519PrivateKey) -> str:
    raw=key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def test_openbao_transit_sign_is_exactly_one_write(monkeypatch):
    key=Ed25519PrivateKey.generate()
    calls=[]

    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","OPENBAO_TRANSIT")
    monkeypatch.setattr(settings,"shrimp_bilibili_openbao_url","https://bao.test")
    monkeypatch.setattr(settings,"shrimp_bilibili_openbao_token","secret-token")
    monkeypatch.setattr(settings,"shrimp_bilibili_openbao_key_name","audit-key")

    def fake_request(method,suffix,*,json=None):
        calls.append((method,suffix,json))
        if method=="GET":
            return {
                "data":{
                    "type":"ed25519",
                    "supports_signing":True,
                    "latest_version":1,
                    "keys":{
                        "1":{
                            "public_key":_public_b64(key),
                            "creation_time":"2026-10-05T00:00:00Z",
                        }
                    },
                }
            }
        digest=base64.b64decode(json["input"]).decode("ascii")
        signature=key.sign(digest.encode("ascii"))
        return {
            "data":{
                "signature":"vault:v1:"+base64.b64encode(signature).decode("ascii")
            }
        }

    monkeypatch.setattr(provider,"_openbao_request",fake_request)
    result=provider.sign_digest_sha256("a"*64)

    assert result.key.provider=="OPENBAO_TRANSIT"
    assert result.key.provider_key_version==1
    assert result.provider_write_count==1
    writes=[x for x in calls if x[0]=="POST"]
    assert len(writes)==1
    assert writes[0][1]=="sign/audit-key"
    assert all("secret-token" not in str(x) for x in calls)


def test_openbao_transit_rotation_advances_version_once(monkeypatch):
    key1=Ed25519PrivateKey.generate()
    key2=Ed25519PrivateKey.generate()
    state={"rotated":False,"rotate_writes":0}

    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","OPENBAO_TRANSIT")
    monkeypatch.setattr(settings,"shrimp_bilibili_openbao_url","https://bao.test")
    monkeypatch.setattr(settings,"shrimp_bilibili_openbao_token","secret-token")
    monkeypatch.setattr(settings,"shrimp_bilibili_openbao_key_name","audit-key")
    monkeypatch.setattr(settings,"shrimp_bilibili_openbao_rotation_enabled",True)

    def fake_request(method,suffix,*,json=None):
        if method=="POST" and suffix=="keys/audit-key/rotate":
            state["rotated"]=True
            state["rotate_writes"]+=1
            return {"data":{}}
        assert method=="GET"
        if state["rotated"]:
            return {
                "data":{
                    "type":"ed25519",
                    "supports_signing":True,
                    "latest_version":2,
                    "keys":{
                        "1":{"public_key":_public_b64(key1)},
                        "2":{"public_key":_public_b64(key2)},
                    },
                }
            }
        return {
            "data":{
                "type":"ed25519",
                "supports_signing":True,
                "latest_version":1,
                "keys":{"1":{"public_key":_public_b64(key1)}},
            }
        }

    monkeypatch.setattr(provider,"_openbao_request",fake_request)
    after=provider.rotate_openbao_signing_key()

    assert state["rotate_writes"]==1
    assert after.provider_key_version==2
    assert after.fingerprint_sha256!=provider._public_material(key1.public_key())[1]


def test_openbao_rotation_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","OPENBAO_TRANSIT")
    monkeypatch.setattr(settings,"shrimp_bilibili_openbao_rotation_enabled",False)
    try:
        provider.rotate_openbao_signing_key()
    except RuntimeError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("OpenBao rotation must remain disabled without explicit enablement")
