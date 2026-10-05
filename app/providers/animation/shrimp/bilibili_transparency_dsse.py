from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509 import load_pem_x509_certificates
from rfc3161_client import VerifierBuilder, decode_timestamp_response
from securesystemslib.dsse import Envelope
from securesystemslib.signer import Signature
from sigstore._internal.timestamp import TimestampAuthorityClient
from sigstore._internal.trust import Keyring, RekorKeyring
from sigstore.models import TransparencyLogEntry
from sigstore_models.common import v1 as common_v1
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_post_restore_certification import _ser,_sha
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    _get_key_by_fingerprint,
    _sslib_key,
    list_trust_roots,
)
from app.providers.animation.shrimp.bilibili_signing_provider import (
    current_signing_key,
    sign_bytes,
)

PAYLOAD_TYPE="application/vnd.in-toto+json"
PREDICATE_TYPE="https://ai-digital-asset-factory.dev/attestation/shrimp-bilibili-proof/v1"


def _get_attestation(attestation_id:UUID) -> dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_dsse_attestations WHERE id=:id
        """),{"id":attestation_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("DSSE attestation not found")
    return _ser(row)


def _signature_rows(attestation_id:UUID) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_dsse_attestation_signatures
          WHERE attestation_id=:id ORDER BY signed_at,id
        """),{"id":attestation_id}).mappings().all()
    return [_ser(x) for x in rows]


def _base_envelope(attestation:dict) -> Envelope:
    return Envelope.from_dict(dict(attestation["dsse_envelope"]))


def _assembled_envelope(attestation:dict) -> Envelope:
    envelope=_base_envelope(attestation)
    for row in _signature_rows(attestation["id"]):
        raw=base64.b64decode(row["signature_b64"].encode("ascii"),validate=True)
        envelope.signatures[row["key_fingerprint_sha256"]]=Signature(
            row["key_fingerprint_sha256"],raw.hex()
        )
    return envelope


def _authorized_keys(attestation:dict) -> list:
    keys=[]
    for fingerprint in attestation["signer_fingerprints"]:
        key=_get_key_by_fingerprint(fingerprint)
        if key is None:
            raise RuntimeError(f"Signing key missing from registry: {fingerprint}")
        keys.append(_sslib_key(key))
    return keys


def create_dsse_attestation(
    *,
    subject_type:str,
    subject_id:str,
    subject_sha256:str,
    predicate:dict[str,Any],
    actor:str,
) -> dict:
    if len(subject_sha256)!=64:
        raise ValueError("Subject SHA-256 must contain 64 hex characters")
    roots=list_trust_roots(limit=1)
    if not roots:
        raise RuntimeError("Current TUF trust root is required")
    root=roots[0]
    statement={
        "_type":"https://in-toto.io/Statement/v1",
        "subject":[{
            "name":f"{subject_type}:{subject_id}",
            "digest":{"sha256":subject_sha256},
        }],
        "predicateType":PREDICATE_TYPE,
        "predicate":{
            **predicate,
            "trustRootVersion":root["root_version"],
            "trustRootSha256":root["root_sha256"],
            "providerWrites":False,
            "productionWrites":False,
        },
    }
    payload=canonical_json(statement).encode("utf-8")
    envelope=Envelope(payload,PAYLOAD_TYPE,{})
    envelope_dict=envelope.to_dict()
    statement_sha=hashlib.sha256(payload).hexdigest()
    envelope_sha=_sha(envelope_dict)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_dsse_attestations(
            subject_type,subject_id,statement_snapshot,statement_sha256,
            dsse_envelope,envelope_sha256,signature_threshold,
            signer_fingerprints,created_by)
          VALUES(
            :subject_type,:subject_id,CAST(:statement AS jsonb),:statement_sha,
            CAST(:envelope AS jsonb),:envelope_sha,:threshold,
            CAST(:fingerprints AS jsonb),:actor)
          RETURNING *
        """),{
            "subject_type":subject_type[:100],"subject_id":subject_id[:500],
            "statement":canonical_json(statement),"statement_sha":statement_sha,
            "envelope":canonical_json(envelope_dict),"envelope_sha":envelope_sha,
            "threshold":root["root_threshold"],
            "fingerprints":canonical_json(root["authorized_key_fingerprints"]),
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def add_dsse_signature(
    attestation_id:UUID,
    *,
    fingerprint:str,
    signature_b64:str,
    public_key_pem_b64:str,
    actor:str,
) -> dict:
    att=_get_attestation(attestation_id)
    if fingerprint not in set(att["signer_fingerprints"]):
        raise ValueError("Signer is not authorized by the attestation TUF root")
    key=_get_key_by_fingerprint(fingerprint)
    if key is None:
        raise LookupError("Signing key not registered")
    if key["public_key_pem_b64"]!=public_key_pem_b64:
        raise ValueError("DSSE public key does not match signing registry")
    envelope=_base_envelope(att)
    raw=base64.b64decode(signature_b64.encode("ascii"),validate=True)
    sig=Signature(fingerprint,raw.hex())
    _sslib_key(key).verify_signature(sig,envelope.pae())
    signature_sha=_sha({
        "attestation_id":str(attestation_id),
        "fingerprint":fingerprint,
        "signature_b64":signature_b64,
    })
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_dsse_attestation_signatures(
            attestation_id,key_fingerprint_sha256,signature_b64,
            public_key_pem_b64,signature_sha256,signed_by)
          VALUES(:id,:fp,:sig,:pub,:sha,:actor)
          RETURNING *
        """),{
            "id":attestation_id,"fp":fingerprint,"sig":signature_b64,
            "pub":public_key_pem_b64,"sha":signature_sha,"actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def sign_dsse_attestation_current(attestation_id:UUID,*,actor:str) -> dict:
    att=_get_attestation(attestation_id)
    key=current_signing_key()
    if key.fingerprint_sha256 not in set(att["signer_fingerprints"]):
        raise RuntimeError("Current signing key is not authorized by attestation TUF root")
    signing=sign_bytes(_base_envelope(att).pae())
    return add_dsse_signature(
        attestation_id,
        fingerprint=signing.key.fingerprint_sha256,
        signature_b64=signing.signature_b64,
        public_key_pem_b64=signing.key.public_key_pem_b64,
        actor=actor,
    )


def verify_dsse_threshold(attestation_id:UUID) -> dict:
    att=_get_attestation(attestation_id)
    envelope=_assembled_envelope(att)
    issues=[]
    accepted={}
    try:
        accepted=envelope.verify(
            _authorized_keys(att),
            int(att["signature_threshold"]),
        )
    except Exception:
        issues.append("DSSE_SIGNATURE_THRESHOLD_NOT_MET")
    return {
        "verification_status":"PASS" if not issues else "FAIL",
        "issue_codes":issues,
        "threshold":att["signature_threshold"],
        "accepted_signer_fingerprints":sorted(accepted),
        "signature_count":len(envelope.signatures),
        "payload_type":envelope.payload_type,
        "securesystemslib_verified":not issues,
    }


def _complete_envelope_dict(attestation_id:UUID) -> dict:
    result=verify_dsse_threshold(attestation_id)
    if result["verification_status"]!="PASS":
        raise RuntimeError("DSSE signature threshold is not met")
    return _assembled_envelope(_get_attestation(attestation_id)).to_dict()


def request_trusted_timestamp(attestation_id:UUID,*,actor:str) -> dict:
    if not settings.shrimp_bilibili_tsa_enabled:
        raise RuntimeError("Sigstore TSA integration is disabled")
    base=settings.shrimp_bilibili_tsa_url.strip().rstrip("/")
    if not base:
        raise RuntimeError("Sigstore TSA URL is not configured")
    envelope=_complete_envelope_dict(attestation_id)
    message=canonical_json(envelope).encode("utf-8")
    client=TimestampAuthorityClient(base+"/api/v1/timestamp")
    response=client.request_timestamp(message)
    with httpx.Client(timeout=max(1.0,settings.shrimp_bilibili_tsa_timeout_seconds)) as http:
        chain_response=http.get(base+"/api/v1/timestamp/certchain")
        chain_response.raise_for_status()
    chain_pem=chain_response.text
    certs=load_pem_x509_certificates(chain_pem.encode("utf-8"))
    if len(certs)<2:
        raise RuntimeError("TSA certificate chain is incomplete")
    builder=VerifierBuilder().tsa_certificate(certs[0]).add_root_certificate(certs[-1])
    for cert in certs[1:-1]:
        builder=builder.add_intermediate_certificate(cert)
    builder.build().verify_message(response,message)
    trusted_time=response.tst_info.gen_time
    response_b64=base64.b64encode(response.as_bytes()).decode("ascii")
    snapshot={
        "schema_version":"shrimp-bilibili-trusted-timestamp-v0.1",
        "attestation_id":str(attestation_id),
        "source":"RFC3161_TSA",
        "trusted_time":trusted_time.isoformat(),
        "message_sha256":hashlib.sha256(message).hexdigest(),
        "sigstore_reference":"sigstore/timestamp-authority",
    }
    sha=_sha(snapshot)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_trusted_timestamps(
            attestation_id,timestamp_source,trusted_time,
            timestamp_response_b64,timestamp_chain_pem,
            timestamp_snapshot,timestamp_sha256,recorded_by)
          VALUES(
            :id,'RFC3161_TSA',:time,:response,:chain,
            CAST(:snapshot AS jsonb),:sha,:actor)
          RETURNING *
        """),{
            "id":attestation_id,"time":trusted_time,"response":response_b64,
            "chain":chain_pem,"snapshot":canonical_json(snapshot),
            "sha":sha,"actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def _rekor_entry_body(envelope:dict,signature_rows:list[dict]) -> dict:
    signatures=[]
    for sig in envelope["signatures"]:
        row=next(
            x for x in signature_rows
            if x["key_fingerprint_sha256"]==sig["keyid"]
        )
        pem=base64.b64decode(row["public_key_pem_b64"].encode("ascii"))
        signatures.append({
            "keyid":sig["keyid"],
            "sig":sig["sig"],
            "publicKey":base64.b64encode(pem).decode("ascii"),
        })
    content_envelope={
        "payload":envelope["payload"],
        "payloadType":envelope["payloadType"],
        "signatures":signatures,
    }
    env_bytes=canonical_json(envelope).encode("utf-8")
    payload=base64.b64decode(envelope["payload"].encode("ascii"))
    return {
        "kind":"intoto",
        "apiVersion":"0.0.2",
        "spec":{"content":{
            "envelope":content_envelope,
            "hash":{"algorithm":"sha256","value":hashlib.sha256(env_bytes).hexdigest()},
            "payloadHash":{"algorithm":"sha256","value":hashlib.sha256(payload).hexdigest()},
        }},
    }


def append_to_rekor(attestation_id:UUID,*,actor:str) -> dict:
    if not settings.shrimp_bilibili_rekor_enabled:
        raise RuntimeError("Rekor integration is disabled")
    base=settings.shrimp_bilibili_rekor_url.strip().rstrip("/")
    if not base:
        raise RuntimeError("Rekor URL is not configured")
    envelope=_complete_envelope_dict(attestation_id)
    signature_rows=_signature_rows(attestation_id)
    proposed=_rekor_entry_body(envelope,signature_rows)
    timeout=max(1.0,settings.shrimp_bilibili_rekor_timeout_seconds)
    with httpx.Client(timeout=timeout) as client:
        pub=client.get(base+"/api/v1/log/publicKey")
        pub.raise_for_status()
        created=client.post(base+"/api/v1/log/entries",json=proposed)
        created.raise_for_status()
    receipt=created.json()
    if len(receipt)!=1:
        raise RuntimeError("Rekor returned an unexpected entry response")
    entry_uuid,entry=next(iter(receipt.items()))
    proof=entry["verification"]["inclusionProof"]
    integrated=datetime.fromtimestamp(int(entry["integratedTime"]),tz=timezone.utc)
    receipt_sha=_sha(receipt)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_transparency_entries(
            attestation_id,provider,entry_uuid,log_index,tree_size,root_hash,
            inclusion_hashes,checkpoint,integrated_time,
            canonicalized_body_b64,receipt_snapshot,receipt_sha256,
            rekor_public_key_pem,recorded_by)
          VALUES(
            :id,'REKOR_V1',:uuid,:index,:size,:root,CAST(:hashes AS jsonb),
            :checkpoint,:integrated,:body,CAST(:receipt AS jsonb),:sha,:pub,:actor)
          RETURNING *
        """),{
            "id":attestation_id,"uuid":entry_uuid,"index":entry["logIndex"],
            "size":proof["treeSize"],"root":proof["rootHash"],
            "hashes":canonical_json(proof["hashes"]),
            "checkpoint":proof["checkpoint"],"integrated":integrated,
            "body":entry["body"],"receipt":canonical_json(receipt),
            "sha":receipt_sha,"pub":pub.text,"actor":actor[:200],
        }).mappings().one()
        ts_snapshot={
            "schema_version":"shrimp-bilibili-trusted-timestamp-v0.1",
            "attestation_id":str(attestation_id),
            "source":"TRANSPARENCY_LOG_INTEGRATED_TIME",
            "trusted_time":integrated.isoformat(),
            "rekor_receipt_sha256":receipt_sha,
        }
        ts_sha=_sha(ts_snapshot)
        db.execute(text("""
          INSERT INTO shrimp_bilibili_trusted_timestamps(
            attestation_id,timestamp_source,trusted_time,
            timestamp_response_b64,timestamp_chain_pem,
            timestamp_snapshot,timestamp_sha256,recorded_by)
          VALUES(
            :id,'TRANSPARENCY_LOG_INTEGRATED_TIME',:time,NULL,NULL,
            CAST(:snapshot AS jsonb),:sha,:actor)
        """),{
            "id":attestation_id,"time":integrated,
            "snapshot":canonical_json(ts_snapshot),"sha":ts_sha,
            "actor":actor[:200],
        })
    return _ser(row)


def _rekor_keyring(public_key_pem:str) -> RekorKeyring:
    key=serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    der=key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if isinstance(key,ed25519.Ed25519PublicKey):
        details=common_v1.PublicKeyDetails.PKIX_ED25519
    elif isinstance(key,ec.EllipticCurvePublicKey):
        if key.curve.name=="secp256r1":
            details=common_v1.PublicKeyDetails.PKIX_ECDSA_P256_SHA_256
        elif key.curve.name=="secp384r1":
            details=common_v1.PublicKeyDetails.PKIX_ECDSA_P384_SHA_384
        else:
            details=common_v1.PublicKeyDetails.PKIX_ECDSA_P521_SHA_512
    elif isinstance(key,rsa.RSAPublicKey):
        details=common_v1.PublicKeyDetails.PKIX_RSA_PKCS1V15_2048_SHA256
    else:
        raise RuntimeError("Unsupported Rekor public key type")
    public=common_v1.PublicKey(raw_bytes=der,key_details=details)
    return RekorKeyring(Keyring([public]))


def _verify_tsa_record(row:dict,envelope_bytes:bytes) -> bool:
    if not row.get("timestamp_response_b64") or not row.get("timestamp_chain_pem"):
        return False
    response=decode_timestamp_response(
        base64.b64decode(row["timestamp_response_b64"].encode("ascii"))
    )
    certs=load_pem_x509_certificates(row["timestamp_chain_pem"].encode("utf-8"))
    if len(certs)<2:
        return False
    builder=VerifierBuilder().tsa_certificate(certs[0]).add_root_certificate(certs[-1])
    for cert in certs[1:-1]:
        builder=builder.add_intermediate_certificate(cert)
    builder.build().verify_message(response,envelope_bytes)
    return True


def verify_offline(attestation_id:UUID) -> dict:
    att=_get_attestation(attestation_id)
    envelope=_complete_envelope_dict(attestation_id)
    envelope_bytes=canonical_json(envelope).encode("utf-8")
    issues=[]
    trusted_times=0
    rekor_verified=False
    with engine.connect() as db:
        timestamps=[
            _ser(x) for x in db.execute(text("""
              SELECT * FROM shrimp_bilibili_trusted_timestamps
              WHERE attestation_id=:id ORDER BY recorded_at,id
            """),{"id":attestation_id}).mappings().all()
        ]
        entries=[
            _ser(x) for x in db.execute(text("""
              SELECT * FROM shrimp_bilibili_transparency_entries
              WHERE attestation_id=:id ORDER BY recorded_at,id
            """),{"id":attestation_id}).mappings().all()
        ]
    for row in entries:
        try:
            entry=TransparencyLogEntry._from_v1_response(row["receipt_snapshot"])
            entry._verify(_rekor_keyring(row["rekor_public_key_pem"]))
            rekor_verified=True
            trusted_times+=1
        except Exception:
            issues.append("REKOR_INCLUSION_OR_CHECKPOINT_INVALID")
    for row in timestamps:
        if row["timestamp_source"]!="RFC3161_TSA":
            continue
        try:
            if _verify_tsa_record(row,envelope_bytes):
                trusted_times+=1
        except Exception:
            issues.append("RFC3161_TIMESTAMP_INVALID")
    if trusted_times<1:
        issues.append("NO_VERIFIED_TRUSTED_TIME")
    if entries and not rekor_verified:
        issues.append("NO_VERIFIED_TRANSPARENCY_ENTRY")
    issues=sorted(set(issues))
    return {
        "verification_status":"PASS" if not issues else "FAIL",
        "issue_codes":issues,
        "attestation_id":str(attestation_id),
        "dsse":verify_dsse_threshold(attestation_id),
        "trusted_time_source_count":trusted_times,
        "rekor_verified":rekor_verified,
        "offline":True,
        "requires_private_key":False,
        "requires_database_write":False,
        "provider_writes":False,
        "production_writes":False,
        "verification_stack":[
            "in-toto/attestation",
            "secure-systems-lab/securesystemslib",
            "sigstore/sigstore-python",
            "sigstore/rekor",
            "sigstore/timestamp-authority",
        ],
    }



def verify_exported_bundle_snapshot(snapshot:dict[str,Any]) -> dict:
    issues=[]
    att=snapshot["attestation"]
    envelope_dict=snapshot["envelope"]
    envelope=Envelope.from_dict(dict(envelope_dict))
    keys=[]
    for row in snapshot["signatures"]:
        try:
            raw_pem=base64.b64decode(row["public_key_pem_b64"].encode("ascii"),validate=True)
            key=serialization.load_pem_public_key(raw_pem)
            der=key.public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            fingerprint=hashlib.sha256(der).hexdigest()
            if fingerprint!=row["key_fingerprint_sha256"]:
                issues.append("DSSE_PUBLIC_KEY_FINGERPRINT_MISMATCH")
                continue
            reg={
                "key_fingerprint_sha256":fingerprint,
                "public_key_pem_b64":row["public_key_pem_b64"],
            }
            keys.append(_sslib_key(reg))
        except Exception:
            issues.append("DSSE_PUBLIC_KEY_INVALID")
    try:
        envelope.verify(keys,int(att["signature_threshold"]))
    except Exception:
        issues.append("DSSE_SIGNATURE_THRESHOLD_NOT_MET")

    envelope_bytes=canonical_json(envelope_dict).encode("utf-8")
    trusted_times=0
    rekor_verified=False
    for row in snapshot.get("transparency_entries",[]):
        try:
            entry=TransparencyLogEntry._from_v1_response(row["receipt_snapshot"])
            entry._verify(_rekor_keyring(row["rekor_public_key_pem"]))
            rekor_verified=True
            trusted_times+=1
        except Exception:
            issues.append("REKOR_INCLUSION_OR_CHECKPOINT_INVALID")
    for row in snapshot.get("trusted_timestamps",[]):
        if row.get("timestamp_source")!="RFC3161_TSA":
            continue
        try:
            if _verify_tsa_record(row,envelope_bytes):
                trusted_times+=1
        except Exception:
            issues.append("RFC3161_TIMESTAMP_INVALID")
    if trusted_times<1:
        issues.append("NO_VERIFIED_TRUSTED_TIME")
    if snapshot.get("transparency_entries") and not rekor_verified:
        issues.append("NO_VERIFIED_TRANSPARENCY_ENTRY")
    issues=sorted(set(issues))
    return {
        "verification_status":"PASS" if not issues else "FAIL",
        "issue_codes":issues,
        "trusted_time_source_count":trusted_times,
        "rekor_verified":rekor_verified,
        "offline":True,
        "requires_private_key":False,
        "requires_database":False,
        "requires_network":False,
    }


def export_offline_bundle(attestation_id:UUID,*,actor:str) -> dict:
    verification=verify_offline(attestation_id)
    att=_get_attestation(attestation_id)
    envelope=_complete_envelope_dict(attestation_id)
    with engine.connect() as db:
        timestamps=[_ser(x) for x in db.execute(text("""
          SELECT * FROM shrimp_bilibili_trusted_timestamps
          WHERE attestation_id=:id ORDER BY recorded_at,id
        """),{"id":attestation_id}).mappings().all()]
        entries=[_ser(x) for x in db.execute(text("""
          SELECT * FROM shrimp_bilibili_transparency_entries
          WHERE attestation_id=:id ORDER BY recorded_at,id
        """),{"id":attestation_id}).mappings().all()]
    def clean(row:dict) -> dict:
        return {
            k:(v.isoformat() if hasattr(v,"isoformat") else v)
            for k,v in row.items()
        }
    snapshot={
        "schema_version":"shrimp-bilibili-offline-verification-bundle-v0.1",
        "attestation":clean(att),
        "envelope":envelope,
        "signatures":[clean(x) for x in _signature_rows(attestation_id)],
        "trusted_timestamps":[clean(x) for x in timestamps],
        "transparency_entries":[clean(x) for x in entries],
        "verification":verification,
    }
    sha=_sha(snapshot)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_offline_verification_bundles(
            attestation_id,bundle_snapshot,bundle_sha256,
            verification_status,issue_codes,generated_by)
          VALUES(
            :id,CAST(:snapshot AS jsonb),:sha,:status,
            CAST(:issues AS jsonb),:actor)
          RETURNING *
        """),{
            "id":attestation_id,"snapshot":canonical_json(snapshot),"sha":sha,
            "status":verification["verification_status"],
            "issues":canonical_json(verification["issue_codes"]),
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def transparency_dashboard() -> dict:
    with engine.connect() as db:
        counts={
            "attestations":db.execute(text("SELECT COUNT(*) FROM shrimp_bilibili_dsse_attestations")).scalar_one(),
            "timestamps":db.execute(text("SELECT COUNT(*) FROM shrimp_bilibili_trusted_timestamps")).scalar_one(),
            "transparency_entries":db.execute(text("SELECT COUNT(*) FROM shrimp_bilibili_transparency_entries")).scalar_one(),
            "offline_bundles":db.execute(text("SELECT COUNT(*) FROM shrimp_bilibili_offline_verification_bundles")).scalar_one(),
        }
    return {
        "counts":counts,
        "rekor_enabled":settings.shrimp_bilibili_rekor_enabled,
        "tsa_enabled":settings.shrimp_bilibili_tsa_enabled,
        "offline_verification":True,
        "private_key_required_for_verification":False,
        "automatic_provider_writes":False,
        "references":{
            "attestation":"in-toto/attestation",
            "dsse":"secure-systems-lab/securesystemslib",
            "trusted_time":"sigstore/sigstore-python + sigstore/timestamp-authority",
            "transparency":"sigstore/rekor",
        },
    }
