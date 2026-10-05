from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any

import pkcs11
from asn1crypto.core import OctetString
from asn1crypto.keys import PrivateKeyAlgorithmId
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pkcs11 import Attribute, KeyType, Mechanism, ObjectClass
from pkcs11.util.ec import encode_named_curve_parameters

from app.config import settings


@dataclass(frozen=True)
class HsmKeyMaterial:
    provider: str
    token_label: str
    key_label: str
    key_id_hex: str
    public_key_pem_b64: str
    fingerprint_sha256: str


def _config(
    *,
    module_path: str | None=None,
    token_label: str | None=None,
    user_pin: str | None=None,
    key_label: str | None=None,
    key_id_hex: str | None=None,
) -> tuple[str,str,str,str,str]:
    module=(module_path or settings.shrimp_bilibili_pkcs11_module).strip()
    token=(token_label or settings.shrimp_bilibili_pkcs11_token_label).strip()
    pin=(user_pin or settings.shrimp_bilibili_pkcs11_user_pin).strip()
    label=(key_label or settings.shrimp_bilibili_pkcs11_key_label).strip()
    key_id=(key_id_hex or settings.shrimp_bilibili_pkcs11_key_id_hex).strip()
    if not all((module,token,pin,label,key_id)):
        raise RuntimeError("PKCS#11 root custody is not fully configured")
    try:
        bytes.fromhex(key_id)
    except ValueError as exc:
        raise ValueError("PKCS#11 key id must be hexadecimal") from exc
    return module,token,pin,label,key_id


def _public_material(pub: Any) -> tuple[str,str]:
    raw=bytes(OctetString.load(pub[Attribute.EC_POINT]))
    key=Ed25519PublicKey.from_public_bytes(raw)
    pem=key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    der=key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(pem).decode("ascii"),hashlib.sha256(der).hexdigest()


def _session(
    *,
    module_path: str | None=None,
    token_label: str | None=None,
    user_pin: str | None=None,
):
    module,token,pin,_,_=_config(
        module_path=module_path,token_label=token_label,user_pin=user_pin
    )
    lib=pkcs11.lib(module)
    return lib.get_token(token_label=token).open(rw=True,user_pin=pin)


def ensure_pkcs11_ed25519_key(
    *,
    module_path: str | None=None,
    token_label: str | None=None,
    user_pin: str | None=None,
    key_label: str | None=None,
    key_id_hex: str | None=None,
) -> HsmKeyMaterial:
    module,token,pin,label,key_id=_config(
        module_path=module_path,token_label=token_label,user_pin=user_pin,
        key_label=key_label,key_id_hex=key_id_hex,
    )
    key_id_bytes=bytes.fromhex(key_id)
    lib=pkcs11.lib(module)
    tok=lib.get_token(token_label=token)
    with tok.open(rw=True,user_pin=pin) as session:
        try:
            pub=session.get_key(
                object_class=ObjectClass.PUBLIC_KEY,
                key_type=KeyType.EC_EDWARDS,
                label=label,id=key_id_bytes,
            )
            session.get_key(
                object_class=ObjectClass.PRIVATE_KEY,
                key_type=KeyType.EC_EDWARDS,
                label=label,id=key_id_bytes,
            )
        except pkcs11.NoSuchKey:
            params=session.create_domain_parameters(
                KeyType.EC_EDWARDS,
                {
                    Attribute.EC_PARAMS:encode_named_curve_parameters(
                        PrivateKeyAlgorithmId.unmap("ed25519")
                    )
                },
                local=True,
            )
            pub,_=params.generate_keypair(
                id=key_id_bytes,label=label,store=True
            )
        pem_b64,fp=_public_material(pub)
    return HsmKeyMaterial(
        provider="PKCS11",
        token_label=token,key_label=label,key_id_hex=key_id,
        public_key_pem_b64=pem_b64,fingerprint_sha256=fp,
    )


def read_pkcs11_key(
    *,
    module_path: str | None=None,
    token_label: str | None=None,
    user_pin: str | None=None,
    key_label: str | None=None,
    key_id_hex: str | None=None,
) -> HsmKeyMaterial:
    module,token,pin,label,key_id=_config(
        module_path=module_path,token_label=token_label,user_pin=user_pin,
        key_label=key_label,key_id_hex=key_id_hex,
    )
    lib=pkcs11.lib(module)
    tok=lib.get_token(token_label=token)
    with tok.open(user_pin=pin) as session:
        pub=session.get_key(
            object_class=ObjectClass.PUBLIC_KEY,key_type=KeyType.EC_EDWARDS,
            label=label,id=bytes.fromhex(key_id),
        )
        pem_b64,fp=_public_material(pub)
    return HsmKeyMaterial(
        provider="PKCS11",token_label=token,key_label=label,key_id_hex=key_id,
        public_key_pem_b64=pem_b64,fingerprint_sha256=fp,
    )


def sign_pkcs11(
    payload: bytes,
    *,
    module_path: str | None=None,
    token_label: str | None=None,
    user_pin: str | None=None,
    key_label: str | None=None,
    key_id_hex: str | None=None,
) -> tuple[bytes,HsmKeyMaterial]:
    material=read_pkcs11_key(
        module_path=module_path,token_label=token_label,user_pin=user_pin,
        key_label=key_label,key_id_hex=key_id_hex,
    )
    module,token,pin,label,key_id=_config(
        module_path=module_path,token_label=token_label,user_pin=user_pin,
        key_label=key_label,key_id_hex=key_id_hex,
    )
    lib=pkcs11.lib(module)
    tok=lib.get_token(token_label=token)
    with tok.open(user_pin=pin) as session:
        private=session.get_key(
            object_class=ObjectClass.PRIVATE_KEY,key_type=KeyType.EC_EDWARDS,
            label=label,id=bytes.fromhex(key_id),
        )
        signature=private.sign(payload,mechanism=Mechanism.EDDSA)
    return bytes(signature),material


def verify_pkcs11_public(
    payload: bytes,signature: bytes,material: HsmKeyMaterial
) -> bool:
    try:
        pem=base64.b64decode(material.public_key_pem_b64.encode("ascii"),validate=True)
        key=serialization.load_pem_public_key(pem)
        if not isinstance(key,Ed25519PublicKey):
            return False
        key.verify(signature,payload)
        return True
    except Exception:
        return False


def pkcs11_readiness() -> dict[str,Any]:
    if settings.shrimp_bilibili_hsm_provider.strip().upper()!="PKCS11":
        return {
            "provider":settings.shrimp_bilibili_hsm_provider.strip().upper() or "DISABLED",
            "configured":False,
            "private_key_exportable":False,
            "issue":"PKCS#11 provider is disabled",
        }
    try:
        key=read_pkcs11_key()
        return {
            "provider":"PKCS11","configured":True,
            "fingerprint_sha256":key.fingerprint_sha256,
            "token_label":key.token_label,"key_label":key.key_label,
            "private_key_exportable":False,"issue":None,
        }
    except Exception as exc:
        return {
            "provider":"PKCS11","configured":False,
            "private_key_exportable":False,"issue":str(exc),
        }
