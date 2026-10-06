from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import settings
from app.providers.animation.shrimp.bilibili_external_kms import (
    AwsKmsProvider,
    AzureKeyVaultProvider,
    ExternalKmsProvider,
    GcpKmsProvider,
)


@dataclass
class LiveCloudResource:
    provider_type: str
    provider_ref: str
    resource_name: str
    resource_locator: dict[str,Any]
    adapter: ExternalKmsProvider


class LiveCloudLifecycle(Protocol):
    provider_type: str
    def create_sacrificial(self,name:str) -> LiveCloudResource: ...
    def disable(self,resource:LiveCloudResource) -> dict[str,Any]: ...
    def cleanup_readback(self,resource:LiveCloudResource) -> dict[str,Any]: ...


def sacrificial_name() -> str:
    prefix=settings.shrimp_bilibili_live_cloud_kms_allowed_name_prefix.strip()
    if not prefix:
        raise RuntimeError("Sacrificial KMS name prefix is not configured")
    return prefix+uuid.uuid4().hex[:12]


def require_sacrificial_name(name:str) -> None:
    prefix=settings.shrimp_bilibili_live_cloud_kms_allowed_name_prefix.strip()
    if not prefix or not name.startswith(prefix):
        raise RuntimeError("Cloud KMS resource is outside sacrificial allowlist")


class AwsLiveKmsLifecycle:
    provider_type="AWS_KMS"

    def __init__(self,client:Any|None=None,region:str|None=None):
        self.region=(region or settings.shrimp_bilibili_live_aws_region).strip()
        if not self.region:
            raise RuntimeError("Live AWS KMS region is not configured")
        if client is None:
            import boto3
            client=boto3.client("kms",region_name=self.region)
        self.client=client

    def create_sacrificial(self,name:str) -> LiveCloudResource:
        require_sacrificial_name(name)
        response=self.client.create_key(
            KeyUsage="SIGN_VERIFY",
            KeySpec="ECC_NIST_P256",
            Description=name,
            Tags=[
                {"TagKey":"shrimp-live-acceptance","TagValue":"true"},
                {"TagKey":"shrimp-sacrificial-name","TagValue":name},
            ],
        )
        meta=response["KeyMetadata"]
        key_id=meta["KeyId"]
        arn=meta.get("Arn") or key_id
        adapter=AwsKmsProvider(
            key_id=key_id,region=self.region,client=self.client,
            provider_ref=f"aws-kms-live:{arn}",
        )
        return LiveCloudResource(
            provider_type=self.provider_type,
            provider_ref=adapter.provider_ref,
            resource_name=name,
            resource_locator={
                "region":self.region,"keyId":key_id,"arn":arn,
                "sacrificialName":name,
            },
            adapter=adapter,
        )

    def disable(self,resource:LiveCloudResource) -> dict[str,Any]:
        require_sacrificial_name(resource.resource_name)
        key_id=resource.resource_locator["keyId"]
        self.client.disable_key(KeyId=key_id)
        scheduled=self.client.schedule_key_deletion(
            KeyId=key_id,PendingWindowInDays=7
        )
        return {
            "disabled":True,
            "deletionScheduled":True,
            "deletionDate":str(scheduled.get("DeletionDate")),
        }

    def cleanup_readback(self,resource:LiveCloudResource) -> dict[str,Any]:
        meta=self.client.describe_key(
            KeyId=resource.resource_locator["keyId"]
        )["KeyMetadata"]
        state=str(meta.get("KeyState") or "")
        return {
            "enabled":bool(meta.get("Enabled")),
            "keyState":state,
            "cleanupVerified":(
                not bool(meta.get("Enabled"))
                and state in {"Disabled","PendingDeletion","Unavailable"}
            ),
        }


class GcpLiveKmsLifecycle:
    provider_type="GCP_KMS"

    def __init__(self,client:Any|None=None):
        self.project=settings.shrimp_bilibili_live_gcp_project_id.strip()
        self.location=settings.shrimp_bilibili_live_gcp_location.strip()
        self.key_ring=settings.shrimp_bilibili_live_gcp_key_ring.strip()
        if not all((self.project,self.location,self.key_ring)):
            raise RuntimeError("Live GCP KMS project/location/key ring is not configured")
        if client is None:
            from google.cloud import kms
            client=kms.KeyManagementServiceClient()
        self.client=client

    def _key_ring_name(self) -> str:
        return self.client.key_ring_path(
            self.project,self.location,self.key_ring
        )

    def create_sacrificial(self,name:str) -> LiveCloudResource:
        require_sacrificial_name(name)
        from google.cloud import kms
        key=self.client.create_crypto_key(request={
            "parent":self._key_ring_name(),
            "crypto_key_id":name,
            "crypto_key":{
                "purpose":kms.CryptoKey.CryptoKeyPurpose.ASYMMETRIC_SIGN,
                "version_template":{
                    "algorithm":kms.CryptoKeyVersion.CryptoKeyVersionAlgorithm.EC_SIGN_P256_SHA256,
                },
                "labels":{"shrimp-live-acceptance":"true"},
            },
        })
        version_name=f"{key.name}/cryptoKeyVersions/1"
        for attempt in range(6):
            version=self.client.get_crypto_key_version(
                request={"name":version_name}
            )
            if version.state==kms.CryptoKeyVersion.CryptoKeyVersionState.ENABLED:
                break
            time.sleep(min(2**attempt,8))
        else:
            raise RuntimeError("GCP KMS sacrificial key version did not become ENABLED")
        adapter=GcpKmsProvider(
            key_version_name=version_name,client=self.client,
            provider_ref=f"gcp-kms-live:{version_name}",
        )
        return LiveCloudResource(
            provider_type=self.provider_type,
            provider_ref=adapter.provider_ref,
            resource_name=name,
            resource_locator={
                "projectId":self.project,"location":self.location,
                "keyRing":self.key_ring,"cryptoKeyName":key.name,
                "keyVersionName":version_name,
                "sacrificialName":name,
            },
            adapter=adapter,
        )

    def disable(self,resource:LiveCloudResource) -> dict[str,Any]:
        require_sacrificial_name(resource.resource_name)
        from google.cloud import kms
        version_name=resource.resource_locator["keyVersionName"]
        updated=self.client.update_crypto_key_version(request={
            "crypto_key_version":{
                "name":version_name,
                "state":kms.CryptoKeyVersion.CryptoKeyVersionState.DISABLED,
            },
            "update_mask":{"paths":["state"]},
        })
        return {"state":str(updated.state),"disabled":True}

    def cleanup_readback(self,resource:LiveCloudResource) -> dict[str,Any]:
        from google.cloud import kms
        version=self.client.get_crypto_key_version(
            request={"name":resource.resource_locator["keyVersionName"]}
        )
        disabled=version.state==kms.CryptoKeyVersion.CryptoKeyVersionState.DISABLED
        return {
            "state":str(version.state),
            "enabled":not disabled,
            "cleanupVerified":disabled,
        }


class AzureLiveKmsLifecycle:
    provider_type="AZURE_KEY_VAULT"

    def __init__(
        self,key_client:Any|None=None,credential:Any|None=None,
        vault_url:str|None=None,
    ):
        self.vault_url=(vault_url or settings.shrimp_bilibili_live_azure_vault_url).strip()
        if not self.vault_url:
            raise RuntimeError("Live Azure Key Vault URL is not configured")
        self.credential=credential
        if key_client is None:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.keys import KeyClient
            self.credential=credential or DefaultAzureCredential()
            key_client=KeyClient(vault_url=self.vault_url,credential=self.credential)
        self.key_client=key_client

    def create_sacrificial(self,name:str) -> LiveCloudResource:
        require_sacrificial_name(name)
        key=self.key_client.create_ec_key(
            name,curve="P-256",enabled=True,
            tags={"shrimp-live-acceptance":"true"},
        )
        adapter=AzureKeyVaultProvider(
            key_id=key.id,credential=self.credential,
            provider_ref=f"azure-key-vault-live:{key.id}",
        )
        return LiveCloudResource(
            provider_type=self.provider_type,
            provider_ref=adapter.provider_ref,
            resource_name=name,
            resource_locator={
                "vaultUrl":self.vault_url,
                "keyName":name,
                "keyId":key.id,
                "keyVersion":key.properties.version,
                "sacrificialName":name,
            },
            adapter=adapter,
        )

    def disable(self,resource:LiveCloudResource) -> dict[str,Any]:
        require_sacrificial_name(resource.resource_name)
        updated=self.key_client.update_key_properties(
            resource.resource_locator["keyName"],
            resource.resource_locator.get("keyVersion"),
            enabled=False,
        )
        return {
            "enabled":bool(updated.properties.enabled),
            "disabled":not bool(updated.properties.enabled),
        }

    def cleanup_readback(self,resource:LiveCloudResource) -> dict[str,Any]:
        key=self.key_client.get_key(
            resource.resource_locator["keyName"],
            resource.resource_locator.get("keyVersion"),
        )
        enabled=bool(key.properties.enabled)
        return {
            "enabled":enabled,
            "cleanupVerified":not enabled,
        }


def default_live_lifecycle(provider_type:str) -> LiveCloudLifecycle:
    if provider_type=="AWS_KMS":
        return AwsLiveKmsLifecycle()
    if provider_type=="GCP_KMS":
        return GcpLiveKmsLifecycle()
    if provider_type=="AZURE_KEY_VAULT":
        return AzureLiveKmsLifecycle()
    raise ValueError(f"Unsupported live cloud KMS provider: {provider_type}")


def live_manifest_digest(provider_type:str,resource:LiveCloudResource) -> bytes:
    payload=(
        "shrimp-bilibili-step10b27-live-kms-v0.1\n"
        +provider_type+"\n"+resource.provider_ref+"\n"
        +resource.resource_name
    ).encode("utf-8")
    return hashlib.sha256(payload).digest()
