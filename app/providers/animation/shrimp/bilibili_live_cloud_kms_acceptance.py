from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_live_cloud_kms import (
    LiveCloudLifecycle,
    LiveCloudResource,
    default_live_lifecycle,
    live_manifest_digest,
    sacrificial_name,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import _ser,_sha
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import list_trust_roots


def _require_live_enabled() -> None:
    if not settings.shrimp_bilibili_live_cloud_kms_acceptance_enabled:
        raise RuntimeError("Live cloud KMS acceptance is disabled")
    if not settings.shrimp_bilibili_live_cloud_kms_cleanup_enabled:
        raise RuntimeError("Live cloud KMS cleanup verification must be enabled")


def _record_acceptance(
    *,
    provider_type:str,
    provider_ref:str,
    resource_locator:dict[str,Any],
    snapshot:dict[str,Any],
    live_signature_verified:bool,
    cleanup_requested:bool,
    cleanup_verified:bool,
    post_cleanup_sign_blocked:bool,
    external_write_count:int,
    status:str,
    actor:str,
) -> dict:
    acceptance_sha=_sha(snapshot)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_live_cloud_kms_acceptance_runs(
            provider_type,provider_ref,resource_locator,acceptance_snapshot,
            acceptance_sha256,live_signature_verified,cleanup_requested,
            cleanup_verified,post_cleanup_sign_blocked,external_write_count,
            acceptance_status,executed_by)
          VALUES(
            :provider_type,:provider_ref,CAST(:locator AS jsonb),
            CAST(:snapshot AS jsonb),:sha,:verified,:cleanup_requested,
            :cleanup_verified,:blocked,:writes,:status,:actor)
          RETURNING *
        """),{
            "provider_type":provider_type,
            "provider_ref":provider_ref,
            "locator":canonical_json(resource_locator),
            "snapshot":canonical_json(snapshot),
            "sha":acceptance_sha,
            "verified":live_signature_verified,
            "cleanup_requested":cleanup_requested,
            "cleanup_verified":cleanup_verified,
            "blocked":post_cleanup_sign_blocked,
            "writes":external_write_count,
            "status":status,
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def run_live_provider_acceptance(
    provider_type:str,
    *,
    actor:str,
    lifecycle:LiveCloudLifecycle|None=None,
) -> dict:
    _require_live_enabled()
    lifecycle=lifecycle or default_live_lifecycle(provider_type)
    name=sacrificial_name()
    resource:LiveCloudResource|None=None
    verified=False
    cleanup_requested=False
    cleanup_verified=False
    post_cleanup_blocked=False
    writes=0
    issues:list[str]=[]
    cleanup_snapshot:dict[str,Any]={}

    try:
        resource=lifecycle.create_sacrificial(name)
        writes+=1
        digest=live_manifest_digest(provider_type,resource)
        signature=resource.adapter.sign_digest(digest)
        writes+=1
        verified=resource.adapter.verify_digest(digest,signature)
        if not verified:
            issues.append("LIVE_SIGNATURE_VERIFICATION_FAILED")

        cleanup_requested=True
        cleanup_snapshot=lifecycle.disable(resource)
        writes+=1
        cleanup_readback=lifecycle.cleanup_readback(resource)
        cleanup_snapshot={
            **cleanup_snapshot,
            "readback":cleanup_readback,
        }
        cleanup_verified=bool(cleanup_readback.get("cleanupVerified"))
        if not cleanup_verified:
            issues.append("CLEANUP_READBACK_FAILED")

        try:
            resource.adapter.sign_digest(digest)
            writes+=1
            issues.append("POST_CLEANUP_SIGN_STILL_ALLOWED")
        except Exception:
            post_cleanup_blocked=True

        if not post_cleanup_blocked:
            issues.append("POST_CLEANUP_SIGN_NOT_BLOCKED")
    except Exception as exc:
        issues.append("LIVE_ACCEPTANCE_EXCEPTION:"+type(exc).__name__)
        if resource is not None and not cleanup_requested:
            try:
                cleanup_requested=True
                cleanup_snapshot=lifecycle.disable(resource)
                writes+=1
                cleanup_readback=lifecycle.cleanup_readback(resource)
                cleanup_snapshot={
                    **cleanup_snapshot,
                    "readback":cleanup_readback,
                }
                cleanup_verified=bool(cleanup_readback.get("cleanupVerified"))
            except Exception as cleanup_exc:
                issues.append("EMERGENCY_CLEANUP_FAILED:"+type(cleanup_exc).__name__)
    if resource is None:
        resource_locator={"sacrificialName":name}
        provider_ref=f"{provider_type}:uncreated:{name}"
    else:
        resource_locator=resource.resource_locator
        provider_ref=resource.provider_ref

    if resource is not None and not cleanup_verified:
        try:
            readback=lifecycle.cleanup_readback(resource)
            if not bool(readback.get("cleanupVerified")):
                cleanup_snapshot=lifecycle.disable(resource)
                writes+=1
                readback=lifecycle.cleanup_readback(resource)
            cleanup_snapshot={
                **cleanup_snapshot,
                "finalReadback":readback,
            }
            cleanup_verified=bool(readback.get("cleanupVerified"))
            if cleanup_verified and not post_cleanup_blocked:
                try:
                    resource.adapter.sign_digest(
                        hashlib.sha256(b"final-post-cleanup-check").digest()
                    )
                    writes+=1
                except Exception:
                    post_cleanup_blocked=True
        except Exception as cleanup_exc:
            issues.append("FINAL_CLEANUP_FAILED:"+type(cleanup_exc).__name__)

    issues=sorted(set(issues))
    if cleanup_verified:
        issues=[x for x in issues if x!="CLEANUP_READBACK_FAILED"]
    if post_cleanup_blocked:
        issues=[x for x in issues if x!="POST_CLEANUP_SIGN_NOT_BLOCKED"]

    status="CLEANUP_VERIFIED" if (
        verified and cleanup_verified and post_cleanup_blocked and not issues
    ) else "FAILED"
    snapshot={
        "schemaVersion":"shrimp-bilibili-live-cloud-kms-acceptance-v0.1",
        "providerType":provider_type,
        "providerRef":provider_ref,
        "resourceLocator":resource_locator,
        "liveSignatureVerified":verified,
        "cleanupRequested":cleanup_requested,
        "cleanupVerified":cleanup_verified,
        "postCleanupSignBlocked":post_cleanup_blocked,
        "cleanup":cleanup_snapshot,
        "issueCodes":issues,
        "externalWriteCount":writes,
        "bilibiliWrites":0,
        "productionWrites":0,
        "credentialsPersisted":False,
    }
    result=_record_acceptance(
        provider_type=provider_type,
        provider_ref=provider_ref,
        resource_locator=resource_locator,
        snapshot=snapshot,
        live_signature_verified=verified,
        cleanup_requested=cleanup_requested,
        cleanup_verified=cleanup_verified,
        post_cleanup_sign_blocked=post_cleanup_blocked,
        external_write_count=writes,
        status=status,
        actor=actor,
    )
    if status!="CLEANUP_VERIFIED":
        raise RuntimeError("Live cloud KMS acceptance failed: "+",".join(issues))
    return result


def _record_outage(
    *,
    primary_ref:str,
    fallback_ref:str,
    digest:bytes,
    snapshot:dict[str,Any],
    primary_failure_observed:bool,
    fallback_signature_verified:bool,
    external_write_count:int,
    actor:str,
) -> dict:
    status="PASSED" if primary_failure_observed and fallback_signature_verified else "FAILED"
    sha=_sha(snapshot)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_live_cloud_kms_outage_drills(
            primary_provider_ref,fallback_provider_ref,request_sha256,
            outage_snapshot,outage_sha256,primary_failure_observed,
            fallback_signature_verified,failover_status,external_write_count,
            executed_by)
          VALUES(
            :primary,:fallback,:request_sha,CAST(:snapshot AS jsonb),:sha,
            :primary_failed,:fallback_verified,:status,:writes,:actor)
          RETURNING *
        """),{
            "primary":primary_ref,
            "fallback":fallback_ref,
            "request_sha":hashlib.sha256(digest).hexdigest(),
            "snapshot":canonical_json(snapshot),
            "sha":sha,
            "primary_failed":primary_failure_observed,
            "fallback_verified":fallback_signature_verified,
            "status":status,
            "writes":external_write_count,
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def run_live_cross_cloud_acceptance(
    provider_types:list[str],
    *,
    threshold:int,
    actor:str,
    lifecycles:dict[str,LiveCloudLifecycle]|None=None,
) -> dict:
    _require_live_enabled()
    unique=list(dict.fromkeys(provider_types))
    if threshold<2:
        raise ValueError("Live cross-cloud threshold must be at least 2")
    if len(unique)<threshold:
        raise ValueError("Not enough distinct cloud provider types")
    allowed={"AWS_KMS","GCP_KMS","AZURE_KEY_VAULT"}
    if any(x not in allowed for x in unique):
        raise ValueError("Unsupported live cloud provider type")

    roots=list_trust_roots(limit=1)
    if not roots:
        raise RuntimeError("Live cross-cloud ceremony requires current TUF root")
    root=roots[0]

    lifecycles=lifecycles or {
        p:default_live_lifecycle(p) for p in unique
    }
    resources:dict[str,LiveCloudResource]={}
    signatures=[]
    cleanup_results={}
    writes=0
    ceremony_error:Exception|None=None
    outage_result:dict|None=None

    try:
        for provider_type in unique:
            lifecycle=lifecycles[provider_type]
            resource=lifecycle.create_sacrificial(sacrificial_name())
            resources[provider_type]=resource
            writes+=1

        manifest={
            "schemaVersion":"shrimp-bilibili-live-cross-cloud-ceremony-v0.1",
            "trustRootVersion":root["root_version"],
            "trustRootSha256":root["root_sha256"],
            "requiredProviderThreshold":threshold,
            "providerTypes":unique,
            "providerRefs":[resources[x].provider_ref for x in unique],
            "privateKeysExported":False,
            "credentialsPersisted":False,
            "productionWrites":0,
            "bilibiliWrites":0,
        }
        digest=hashlib.sha256(canonical_json(manifest).encode("utf-8")).digest()

        valid=0
        for provider_type in unique:
            resource=resources[provider_type]
            sig=resource.adapter.sign_digest(digest)
            writes+=1
            verified=resource.adapter.verify_digest(digest,sig)
            signatures.append({
                "providerType":provider_type,
                "providerRef":resource.provider_ref,
                "signatureB64":sig.signature_b64,
                "keyVersion":sig.key_version,
                "verified":verified,
            })
            if verified:
                valid+=1
        if valid<threshold:
            raise RuntimeError("Live cross-cloud signature threshold not met")

        primary_type=unique[0]
        fallback_type=unique[1]
        primary=resources[primary_type]
        fallback=resources[fallback_type]
        primary_lifecycle=lifecycles[primary_type]
        primary_lifecycle.disable(primary)
        writes+=1

        primary_failure=False
        try:
            primary.adapter.sign_digest(digest)
            writes+=1
        except Exception:
            primary_failure=True

        fallback_sig=fallback.adapter.sign_digest(digest)
        writes+=1
        fallback_verified=fallback.adapter.verify_digest(digest,fallback_sig)
        outage_snapshot={
            "schemaVersion":"shrimp-bilibili-live-kms-outage-drill-v0.1",
            "primaryProviderType":primary_type,
            "primaryProviderRef":primary.provider_ref,
            "fallbackProviderType":fallback_type,
            "fallbackProviderRef":fallback.provider_ref,
            "primaryFailureObserved":primary_failure,
            "fallbackSignatureVerified":fallback_verified,
            "productionWrites":0,
            "bilibiliWrites":0,
        }
        outage_result=_record_outage(
            primary_ref=primary.provider_ref,
            fallback_ref=fallback.provider_ref,
            digest=digest,
            snapshot=outage_snapshot,
            primary_failure_observed=primary_failure,
            fallback_signature_verified=fallback_verified,
            external_write_count=3,
            actor=actor+"-outage",
        )
        if outage_result["failover_status"]!="PASSED":
            raise RuntimeError("Live cross-cloud outage failover drill failed")

        ceremony_snapshot={
            **manifest,
            "validProviderSignatures":valid,
            "outageDrillId":str(outage_result["id"]),
            "externalWriteCountBeforeCleanup":writes,
        }
        ceremony=None
    except Exception as exc:
        ceremony_error=exc
        ceremony=None
    finally:
        for provider_type,resource in resources.items():
            lifecycle=lifecycles[provider_type]
            try:
                initial_readback=lifecycle.cleanup_readback(resource)
                if bool(initial_readback.get("cleanupVerified")):
                    disable_result={"alreadyCleaned":True}
                    readback=initial_readback
                else:
                    disable_result=lifecycle.disable(resource)
                    writes+=1
                    readback=lifecycle.cleanup_readback(resource)
                blocked=False
                try:
                    resource.adapter.sign_digest(
                        hashlib.sha256(b"post-cleanup-check").digest()
                    )
                    writes+=1
                except Exception:
                    blocked=True
                cleanup_results[provider_type]={
                    "disable":disable_result,
                    "readback":readback,
                    "postCleanupSignBlocked":blocked,
                }
            except Exception as cleanup_exc:
                cleanup_results[provider_type]={
                    "cleanupVerified":False,
                    "issue":type(cleanup_exc).__name__,
                }

    cleanup_ok=bool(resources) and all(
        bool(v.get("readback",{}).get("cleanupVerified"))
        and bool(v.get("postCleanupSignBlocked"))
        for v in cleanup_results.values()
    )
    if ceremony_error is not None:
        raise RuntimeError(
            "Live cross-cloud ceremony failed before cleanup: "
            +type(ceremony_error).__name__
        ) from ceremony_error
    if not cleanup_ok:
        raise RuntimeError("Live cross-cloud cleanup verification failed")

    ceremony_snapshot={
        **ceremony_snapshot,
        "cleanupVerified":True,
        "cleanup":cleanup_results,
        "externalWriteCountFinal":writes,
    }
    ceremony_sha=_sha({
        "snapshot":ceremony_snapshot,
        "signatures":signatures,
    })
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_live_cross_cloud_ceremonies(
            trust_root_version,trust_root_sha256,required_provider_threshold,
            live_provider_refs,provider_signatures,ceremony_snapshot,
            ceremony_sha256,ceremony_status,external_write_count,executed_by)
          VALUES(
            :version,:root_sha,:threshold,CAST(:refs AS jsonb),
            CAST(:signatures AS jsonb),CAST(:snapshot AS jsonb),
            :sha,'PASSED',:writes,:actor)
          RETURNING *
        """),{
            "version":root["root_version"],
            "root_sha":root["root_sha256"],
            "threshold":threshold,
            "refs":canonical_json([resources[x].provider_ref for x in unique]),
            "signatures":canonical_json(signatures),
            "snapshot":canonical_json(ceremony_snapshot),
            "sha":ceremony_sha,
            "writes":writes,
            "actor":actor[:200],
        }).mappings().one()
    ceremony=_ser(row)

    return {
        "status":"PASSED",
        "ceremony":ceremony,
        "outage_drill":outage_result,
        "cleanup":cleanup_results,
        "external_write_count":writes,
        "credentials_persisted":False,
        "private_key_export_allowed":False,
        "production_writes":0,
        "bilibili_writes":0,
    }


def live_cloud_kms_dashboard() -> dict[str,Any]:
    with engine.connect() as db:
        acceptances=db.execute(text("""
          SELECT * FROM shrimp_bilibili_live_cloud_kms_acceptance_runs
          ORDER BY executed_at DESC LIMIT 100
        """)).mappings().all()
        outages=db.execute(text("""
          SELECT * FROM shrimp_bilibili_live_cloud_kms_outage_drills
          ORDER BY executed_at DESC LIMIT 100
        """)).mappings().all()
        ceremonies=db.execute(text("""
          SELECT * FROM shrimp_bilibili_live_cross_cloud_ceremonies
          ORDER BY executed_at DESC LIMIT 100
        """)).mappings().all()
    acceptance_rows=[_ser(x) for x in acceptances]
    outage_rows=[_ser(x) for x in outages]
    ceremony_rows=[_ser(x) for x in ceremonies]
    accepted_provider_types=sorted({
        x["provider_type"]
        for x in acceptance_rows
        if x["acceptance_status"]=="CLEANUP_VERIFIED"
        and x["live_signature_verified"]
        and x["cleanup_verified"]
        and x["post_cleanup_sign_blocked"]
    })
    return {
        "live_acceptance_enabled":settings.shrimp_bilibili_live_cloud_kms_acceptance_enabled,
        "cleanup_verification_enabled":settings.shrimp_bilibili_live_cloud_kms_cleanup_enabled,
        "sacrificial_name_prefix":settings.shrimp_bilibili_live_cloud_kms_allowed_name_prefix,
        "acceptances":acceptance_rows,
        "outage_drills":outage_rows,
        "cross_cloud_ceremonies":ceremony_rows,
        "accepted_provider_types":accepted_provider_types,
        "live_cloud_account_acceptance_completed":bool(accepted_provider_types),
        "live_cross_cloud_acceptance_completed":any(
            x["ceremony_status"]=="PASSED" for x in ceremony_rows
        ),
        "credentials_persisted":False,
        "private_key_export_allowed":False,
        "automatic_production_writes":False,
    }
