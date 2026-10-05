from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


@dataclass(frozen=True)
class ExternalKmsKeyInfo:
    provider_type: str
    provider_ref: str
    signing_algorithm: str
    public_key_pem_b64: str | None
    public_key_fingerprint_sha256: str | None
    key_version: str | None = None


@dataclass(frozen=True)
class ExternalKmsSignature:
    provider_ref: str
    signature_b64: str
    key_version: str | None
    algorithm: str = "ECDSA_P256_SHA256"


class ExternalKmsProvider(Protocol):
    provider_type: str
    provider_ref: str

    def key_info(self) -> ExternalKmsKeyInfo: ...
    def sign_digest(self,digest:bytes) -> ExternalKmsSignature: ...
    def verify_digest(self,digest:bytes,signature:ExternalKmsSignature) -> bool: ...
    def health(self) -> dict[str,Any]: ...


def _fingerprint_pem(public_key_pem:bytes) -> str:
    key=serialization.load_pem_public_key(public_key_pem)
    der=key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


class AwsKmsProvider:
    provider_type="AWS_KMS"

    def __init__(
        self,*,key_id:str,region:str,client:Any|None=None,provider_ref:str|None=None
    ):
        self.key_id=key_id
        self.region=region
        self.provider_ref=provider_ref or f"aws-kms:{region}:{key_id}"
        if client is None:
            import boto3
            client=boto3.client("kms",region_name=region)
        self.client=client

    def key_info(self) -> ExternalKmsKeyInfo:
        response=self.client.get_public_key(KeyId=self.key_id)
        pem=serialization.load_der_public_key(response["PublicKey"]).public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return ExternalKmsKeyInfo(
            provider_type=self.provider_type,
            provider_ref=self.provider_ref,
            signing_algorithm="ECDSA_P256_SHA256",
            public_key_pem_b64=base64.b64encode(pem).decode("ascii"),
            public_key_fingerprint_sha256=_fingerprint_pem(pem),
            key_version=str(response.get("KeyId") or self.key_id),
        )

    def sign_digest(self,digest:bytes) -> ExternalKmsSignature:
        response=self.client.sign(
            KeyId=self.key_id,
            Message=digest,
            MessageType="DIGEST",
            SigningAlgorithm="ECDSA_SHA_256",
        )
        return ExternalKmsSignature(
            provider_ref=self.provider_ref,
            signature_b64=base64.b64encode(response["Signature"]).decode("ascii"),
            key_version=str(response.get("KeyId") or self.key_id),
        )

    def verify_digest(self,digest:bytes,signature:ExternalKmsSignature) -> bool:
        response=self.client.verify(
            KeyId=self.key_id,
            Message=digest,
            MessageType="DIGEST",
            Signature=base64.b64decode(signature.signature_b64),
            SigningAlgorithm="ECDSA_SHA_256",
        )
        return bool(response.get("SignatureValid"))

    def health(self) -> dict[str,Any]:
        try:
            info=self.key_info()
            return {"healthy":True,"provider_ref":self.provider_ref,"fingerprint":info.public_key_fingerprint_sha256}
        except Exception as exc:
            return {"healthy":False,"provider_ref":self.provider_ref,"issue":str(exc)}


class GcpKmsProvider:
    provider_type="GCP_KMS"

    def __init__(
        self,*,key_version_name:str,client:Any|None=None,provider_ref:str|None=None
    ):
        self.key_version_name=key_version_name
        self.provider_ref=provider_ref or f"gcp-kms:{key_version_name}"
        if client is None:
            from google.cloud import kms_v1
            client=kms_v1.KeyManagementServiceClient()
        self.client=client

    def key_info(self) -> ExternalKmsKeyInfo:
        response=self.client.get_public_key(request={"name":self.key_version_name})
        pem=response.pem.encode("utf-8")
        return ExternalKmsKeyInfo(
            provider_type=self.provider_type,
            provider_ref=self.provider_ref,
            signing_algorithm="ECDSA_P256_SHA256",
            public_key_pem_b64=base64.b64encode(pem).decode("ascii"),
            public_key_fingerprint_sha256=_fingerprint_pem(pem),
            key_version=self.key_version_name,
        )

    def sign_digest(self,digest:bytes) -> ExternalKmsSignature:
        from google.cloud import kms_v1
        response=self.client.asymmetric_sign(
            request={
                "name":self.key_version_name,
                "digest":kms_v1.Digest(sha256=digest),
            }
        )
        return ExternalKmsSignature(
            provider_ref=self.provider_ref,
            signature_b64=base64.b64encode(response.signature).decode("ascii"),
            key_version=self.key_version_name,
        )

    def verify_digest(self,digest:bytes,signature:ExternalKmsSignature) -> bool:
        try:
            info=self.key_info()
            pem=base64.b64decode(info.public_key_pem_b64 or "")
            key=serialization.load_pem_public_key(pem)
            if not isinstance(key,ec.EllipticCurvePublicKey):
                return False
            key.verify(
                base64.b64decode(signature.signature_b64),
                digest,
                ec.ECDSA(hashes.SHA256()),
            )
            return True
        except Exception:
            return False

    def health(self) -> dict[str,Any]:
        try:
            info=self.key_info()
            return {"healthy":True,"provider_ref":self.provider_ref,"fingerprint":info.public_key_fingerprint_sha256}
        except Exception as exc:
            return {"healthy":False,"provider_ref":self.provider_ref,"issue":str(exc)}


class AzureKeyVaultProvider:
    provider_type="AZURE_KEY_VAULT"

    def __init__(
        self,*,key_id:str,client:Any|None=None,credential:Any|None=None,
        provider_ref:str|None=None,
    ):
        self.key_id=key_id
        self.provider_ref=provider_ref or f"azure-key-vault:{key_id}"
        if client is None:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.keys.crypto import CryptographyClient
            credential=credential or DefaultAzureCredential()
            client=CryptographyClient(key_id,credential=credential)
        self.client=client

    def key_info(self) -> ExternalKmsKeyInfo:
        key=getattr(self.client,"key",None)
        pem=None
        fingerprint=None
        if key is not None and hasattr(key,"key") and key.key is not None:
            jwk=key.key
            if getattr(jwk,"x",None) and getattr(jwk,"y",None):
                public_numbers=ec.EllipticCurvePublicNumbers(
                    int.from_bytes(jwk.x,"big"),
                    int.from_bytes(jwk.y,"big"),
                    ec.SECP256R1(),
                )
                public=public_numbers.public_key()
                pem=public.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                fingerprint=_fingerprint_pem(pem)
        return ExternalKmsKeyInfo(
            provider_type=self.provider_type,
            provider_ref=self.provider_ref,
            signing_algorithm="ECDSA_P256_SHA256",
            public_key_pem_b64=base64.b64encode(pem).decode("ascii") if pem else None,
            public_key_fingerprint_sha256=fingerprint,
            key_version=self.key_id.rsplit("/",1)[-1] if "/" in self.key_id else None,
        )

    def sign_digest(self,digest:bytes) -> ExternalKmsSignature:
        from azure.keyvault.keys.crypto import SignatureAlgorithm
        result=self.client.sign(SignatureAlgorithm.es256,digest)
        return ExternalKmsSignature(
            provider_ref=self.provider_ref,
            signature_b64=base64.b64encode(result.signature).decode("ascii"),
            key_version=self.key_id.rsplit("/",1)[-1] if "/" in self.key_id else None,
        )

    def verify_digest(self,digest:bytes,signature:ExternalKmsSignature) -> bool:
        from azure.keyvault.keys.crypto import SignatureAlgorithm
        result=self.client.verify(
            SignatureAlgorithm.es256,
            digest,
            base64.b64decode(signature.signature_b64),
        )
        return bool(result.is_valid)

    def health(self) -> dict[str,Any]:
        try:
            self.key_info()
            return {"healthy":True,"provider_ref":self.provider_ref}
        except Exception as exc:
            return {"healthy":False,"provider_ref":self.provider_ref,"issue":str(exc)}


class OpenBaoExternalKeyProvider:
    provider_type="OPENBAO_EXTERNAL_KEY"

    def __init__(
        self,*,base_url:str,token:str,key_name:str,mount:str="transit",
        client:Any|None=None,provider_ref:str|None=None,
    ):
        self.base_url=base_url.rstrip("/")
        self.token=token
        self.key_name=key_name
        self.mount=mount.strip("/")
        self.provider_ref=provider_ref or f"openbao-external-key:{self.mount}:{key_name}"
        self.client=client or httpx.Client(timeout=10.0)

    def _request(self,method:str,path:str,**kwargs):
        return self.client.request(
            method,
            self.base_url+path,
            headers={"X-Vault-Token":self.token},
            **kwargs,
        )

    def key_info(self) -> ExternalKmsKeyInfo:
        response=self._request("GET",f"/v1/{self.mount}/keys/{self.key_name}")
        response.raise_for_status()
        data=response.json()["data"]
        version=str(data["latest_version"])
        pub=(data.get("keys") or {}).get(version,{}).get("public_key")
        pem=pub.encode("utf-8") if pub else None
        return ExternalKmsKeyInfo(
            provider_type=self.provider_type,
            provider_ref=self.provider_ref,
            signing_algorithm="ECDSA_P256_SHA256",
            public_key_pem_b64=base64.b64encode(pem).decode("ascii") if pem else None,
            public_key_fingerprint_sha256=_fingerprint_pem(pem) if pem else None,
            key_version=version,
        )

    def sign_digest(self,digest:bytes) -> ExternalKmsSignature:
        response=self._request(
            "POST",f"/v1/{self.mount}/sign/{self.key_name}",
            json={
                "input":base64.b64encode(digest).decode("ascii"),
                "prehashed":True,
                "hash_algorithm":"sha2-256",
            },
        )
        response.raise_for_status()
        raw=response.json()["data"]["signature"]
        return ExternalKmsSignature(
            provider_ref=self.provider_ref,
            signature_b64=base64.b64encode(raw.encode("utf-8")).decode("ascii"),
            key_version=raw.split(":")[1] if raw.startswith("vault:v") else None,
        )

    def verify_digest(self,digest:bytes,signature:ExternalKmsSignature) -> bool:
        raw=base64.b64decode(signature.signature_b64).decode("utf-8")
        response=self._request(
            "POST",f"/v1/{self.mount}/verify/{self.key_name}",
            json={
                "input":base64.b64encode(digest).decode("ascii"),
                "prehashed":True,
                "hash_algorithm":"sha2-256",
                "signature":raw,
            },
        )
        response.raise_for_status()
        return bool(response.json()["data"]["valid"])

    def health(self) -> dict[str,Any]:
        try:
            info=self.key_info()
            return {"healthy":True,"provider_ref":self.provider_ref,"fingerprint":info.public_key_fingerprint_sha256}
        except Exception as exc:
            return {"healthy":False,"provider_ref":self.provider_ref,"issue":str(exc)}
