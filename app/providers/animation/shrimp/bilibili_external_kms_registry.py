from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_external_kms import (
    AwsKmsProvider,
    AzureKeyVaultProvider,
    ExternalKmsProvider,
    ExternalKmsSignature,
    GcpKmsProvider,
    OpenBaoExternalKeyProvider,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import _ser,_sha
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import list_trust_roots


def register_external_kms_provider(
    *,
    provider_type:str,
    provider_ref:str,
    key_locator:dict[str,Any],
    priority:int,
    actor:str,
    signing_algorithm:str="ECDSA_P256_SHA256",
    public_key_pem_b64:str|None=None,
    public_key_fingerprint_sha256:str|None=None,
) -> dict:
    with engine.begin() as db:
        existing=db.execute(text("""
          SELECT * FROM shrimp_bilibili_external_kms_providers
          WHERE provider_ref=:ref
        """),{"ref":provider_ref}).mappings().one_or_none()
        if existing is not None:
            return _ser(existing)
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_external_kms_providers(
            provider_type,provider_ref,key_locator,signing_algorithm,
            priority,enabled,public_key_pem_b64,public_key_fingerprint_sha256,
            registered_by)
          VALUES(
            :type,:ref,CAST(:locator AS jsonb),:algorithm,:priority,true,
            :public_key,:fingerprint,:actor)
          RETURNING *
        """),{
            "type":provider_type,
            "ref":provider_ref,
            "locator":canonical_json(key_locator),
            "algorithm":signing_algorithm,
            "priority":priority,
            "public_key":public_key_pem_b64,
            "fingerprint":public_key_fingerprint_sha256,
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def list_external_kms_providers(*,limit:int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_external_kms_providers
          ORDER BY priority,registered_at,id
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def record_provider_event(
    provider_id,*,event_type:str,snapshot:dict[str,Any],actor:str
) -> dict:
    event_sha=_sha({
        "provider_id":str(provider_id),
        "event_type":event_type,
        "snapshot":snapshot,
    })
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_external_kms_provider_events(
            provider_id,event_type,event_snapshot,event_sha256,recorded_by)
          VALUES(
            :provider,:type,CAST(:snapshot AS jsonb),:sha,:actor)
          RETURNING *
        """),{
            "provider":provider_id,
            "type":event_type,
            "snapshot":canonical_json(snapshot),
            "sha":event_sha,
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def configured_adapters() -> dict[str,ExternalKmsProvider]:
    result:dict[str,ExternalKmsProvider]={}
    if settings.shrimp_bilibili_aws_kms_key_id.strip():
        p=AwsKmsProvider(
            key_id=settings.shrimp_bilibili_aws_kms_key_id.strip(),
            region=settings.shrimp_bilibili_aws_kms_region.strip() or "us-east-1",
        )
        result[p.provider_ref]=p
    if settings.shrimp_bilibili_gcp_kms_key_version.strip():
        p=GcpKmsProvider(
            key_version_name=settings.shrimp_bilibili_gcp_kms_key_version.strip()
        )
        result[p.provider_ref]=p
    if settings.shrimp_bilibili_azure_key_id.strip():
        p=AzureKeyVaultProvider(
            key_id=settings.shrimp_bilibili_azure_key_id.strip()
        )
        result[p.provider_ref]=p
    if (
        settings.shrimp_bilibili_openbao_external_key_name.strip()
        and settings.shrimp_bilibili_openbao_url.strip()
        and settings.shrimp_bilibili_openbao_token.strip()
    ):
        p=OpenBaoExternalKeyProvider(
            base_url=settings.shrimp_bilibili_openbao_url,
            token=settings.shrimp_bilibili_openbao_token,
            key_name=settings.shrimp_bilibili_openbao_external_key_name.strip(),
            mount=settings.shrimp_bilibili_openbao_transit_mount,
        )
        result[p.provider_ref]=p
    return result


def sync_configured_provider_registry(*,actor:str) -> list[dict]:
    rows=[]
    for ref,adapter in configured_adapters().items():
        info=adapter.key_info()
        locator={
            "providerRef":ref,
            "keyVersion":info.key_version,
        }
        if adapter.provider_type=="AWS_KMS":
            locator.update({"keyId":adapter.key_id,"region":adapter.region})
        elif adapter.provider_type=="GCP_KMS":
            locator.update({"keyVersionName":adapter.key_version_name})
        elif adapter.provider_type=="AZURE_KEY_VAULT":
            locator.update({"keyId":adapter.key_id})
        elif adapter.provider_type=="OPENBAO_EXTERNAL_KEY":
            locator.update({
                "baseUrl":adapter.base_url,
                "mount":adapter.mount,
                "keyName":adapter.key_name,
            })
        rows.append(register_external_kms_provider(
            provider_type=adapter.provider_type,
            provider_ref=ref,
            key_locator=locator,
            priority=100+len(rows),
            actor=actor,
            public_key_pem_b64=info.public_key_pem_b64,
            public_key_fingerprint_sha256=info.public_key_fingerprint_sha256,
        ))
    return rows


def sign_with_failover(
    digest:bytes,
    *,
    actor:str,
    adapters:dict[str,ExternalKmsProvider]|None=None,
) -> tuple[ExternalKmsSignature,dict]:
    if not settings.shrimp_bilibili_external_kms_failover_enabled and adapters is None:
        raise RuntimeError("External KMS failover is disabled")
    adapters=adapters or configured_adapters()
    registrations=list_external_kms_providers(limit=500)
    attempted=[]
    selected=None
    signature=None
    errors={}
    ordered=[
        row for row in registrations
        if row["enabled"] and row["provider_ref"] in adapters
    ]
    for row in ordered:
        ref=row["provider_ref"]
        attempted.append(ref)
        adapter=adapters[ref]
        health=adapter.health()
        if not health.get("healthy"):
            errors[ref]=health.get("issue","UNHEALTHY")
            record_provider_event(
                row["id"],event_type="FAILOVER_SKIPPED",
                snapshot={"providerRef":ref,"reason":errors[ref]},
                actor=actor,
            )
            continue
        try:
            candidate=adapter.sign_digest(digest)
            if not adapter.verify_digest(digest,candidate):
                raise RuntimeError("provider signature verification failed")
            signature=candidate
            selected=ref
            record_provider_event(
                row["id"],event_type="FAILOVER_SELECTED",
                snapshot={"providerRef":ref,"verified":True},
                actor=actor,
            )
            break
        except Exception as exc:
            errors[ref]=str(exc)
            record_provider_event(
                row["id"],event_type="FAILOVER_SKIPPED",
                snapshot={"providerRef":ref,"reason":str(exc)},
                actor=actor,
            )
    snapshot={
        "schemaVersion":"shrimp-bilibili-external-kms-failover-v0.1",
        "requestSha256":hashlib.sha256(digest).hexdigest(),
        "attemptedProviderRefs":attempted,
        "selectedProviderRef":selected,
        "errors":errors,
        "providerWrites":0,
        "productionWrites":0,
        "bilibiliWrites":0,
    }
    failover_sha=_sha(snapshot)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_external_kms_failover_runs(
            request_sha256,attempted_provider_refs,selected_provider_ref,
            failover_status,failover_snapshot,failover_sha256,executed_by)
          VALUES(
            :request_sha,CAST(:attempted AS jsonb),:selected,:status,
            CAST(:snapshot AS jsonb),:sha,:actor)
          RETURNING *
        """),{
            "request_sha":snapshot["requestSha256"],
            "attempted":canonical_json(attempted),
            "selected":selected,
            "status":"SUCCEEDED" if signature else "FAILED",
            "snapshot":canonical_json(snapshot),
            "sha":failover_sha,
            "actor":actor[:200],
        }).mappings().one()
    result=_ser(row)
    if signature is None:
        raise RuntimeError("No healthy external KMS provider could sign")
    return signature,result


def create_cross_kms_root_ceremony(
    *,
    provider_refs:list[str],
    threshold:int,
    actor:str,
    adapters:dict[str,ExternalKmsProvider]|None=None,
) -> dict:
    roots=list_trust_roots(limit=1)
    if not roots:
        raise RuntimeError("Cross-KMS ceremony requires current TUF root")
    if threshold<2:
        raise ValueError("Cross-KMS ceremony threshold must be at least 2")
    adapters=adapters or configured_adapters()
    registrations={
        row["provider_ref"]:row for row in list_external_kms_providers(limit=500)
    }
    selected=[]
    provider_types=set()
    for ref in dict.fromkeys(provider_refs):
        if ref not in adapters:
            raise LookupError(f"Provider adapter not available: {ref}")
        if ref not in registrations:
            raise LookupError(f"Provider not registered: {ref}")
        row=registrations[ref]
        if not row["enabled"]:
            raise RuntimeError(f"Provider disabled: {ref}")
        selected.append((row,adapters[ref]))
        provider_types.add(row["provider_type"])
    if len(selected)<threshold:
        raise ValueError("Not enough distinct providers for threshold")
    if len(provider_types)<threshold:
        raise ValueError("Cross-KMS threshold requires distinct provider types")

    root=roots[0]
    manifest={
        "schemaVersion":"shrimp-bilibili-cross-kms-root-ceremony-v0.1",
        "trustRootVersion":root["root_version"],
        "trustRootSha256":root["root_sha256"],
        "requiredProviderThreshold":threshold,
        "providerRefs":[row["provider_ref"] for row,_ in selected],
        "providerTypes":sorted(provider_types),
        "signingAlgorithm":"ECDSA_P256_SHA256",
        "privateKeysExported":False,
        "productionWrites":0,
        "bilibiliWrites":0,
    }
    digest=hashlib.sha256(canonical_json(manifest).encode("utf-8")).digest()
    signatures=[]
    valid=0
    for row,adapter in selected:
        sig=adapter.sign_digest(digest)
        verified=adapter.verify_digest(digest,sig)
        signatures.append({
            "providerRef":row["provider_ref"],
            "providerType":row["provider_type"],
            "signatureB64":sig.signature_b64,
            "keyVersion":sig.key_version,
            "verified":verified,
        })
        if verified:
            valid+=1
    status="PASSED" if valid>=threshold else "FAILED"
    manifest["validProviderSignatures"]=valid
    ceremony_sha=_sha({"manifest":manifest,"signatures":signatures})
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_cross_kms_root_ceremonies(
            trust_root_version,trust_root_sha256,required_provider_threshold,
            provider_signatures,ceremony_manifest,ceremony_sha256,
            ceremony_status,executed_by)
          VALUES(
            :version,:root_sha,:threshold,CAST(:signatures AS jsonb),
            CAST(:manifest AS jsonb),:sha,:status,:actor)
          RETURNING *
        """),{
            "version":root["root_version"],
            "root_sha":root["root_sha256"],
            "threshold":threshold,
            "signatures":canonical_json(signatures),
            "manifest":canonical_json(manifest),
            "sha":ceremony_sha,
            "status":status,
            "actor":actor[:200],
        }).mappings().one()
    result=_ser(row)
    if status!="PASSED":
        raise RuntimeError("Cross-KMS root ceremony threshold not met")
    return result


def external_kms_dashboard() -> dict[str,Any]:
    providers=list_external_kms_providers(limit=100)
    with engine.connect() as db:
        failovers=db.execute(text("""
          SELECT * FROM shrimp_bilibili_external_kms_failover_runs
          ORDER BY executed_at DESC LIMIT 100
        """)).mappings().all()
        ceremonies=db.execute(text("""
          SELECT * FROM shrimp_bilibili_cross_kms_root_ceremonies
          ORDER BY executed_at DESC LIMIT 100
        """)).mappings().all()
    return {
        "providers":providers,
        "failover_runs":[_ser(x) for x in failovers],
        "cross_kms_ceremonies":[_ser(x) for x in ceremonies],
        "common_algorithm":"ECDSA_P256_SHA256",
        "external_kms_enabled":settings.shrimp_bilibili_external_kms_enabled,
        "failover_enabled":settings.shrimp_bilibili_external_kms_failover_enabled,
        "credentials_persisted":False,
        "private_key_export_allowed":False,
        "automatic_production_writes":False,
        "references":{
            "aws":"aws/aws-sdk-python / boto3",
            "gcp":"googleapis/google-cloud-python / google-cloud-kms",
            "azure":"Azure/azure-sdk-for-python / azure-keyvault-keys",
            "openbao":"openbao/openbao external-key architecture",
        },
    }
