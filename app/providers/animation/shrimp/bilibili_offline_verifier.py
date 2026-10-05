from __future__ import annotations

import base64
import copy
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.x509 import load_pem_x509_certificates
from rfc3161_client import VerifierBuilder, decode_timestamp_response
from securesystemslib.dsse import Envelope
from securesystemslib.signer import SSlibKey, Signature


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",",":"),
        ensure_ascii=False,
    )


def rfc6962_leaf_hash(payload: bytes) -> bytes:
    return hashlib.sha256(b"\x00"+payload).digest()


def rfc6962_node_hash(left: bytes,right: bytes) -> bytes:
    return hashlib.sha256(b"\x01"+left+right).digest()


def _split_point(size:int) -> int:
    if size<2:
        raise ValueError("tree size must be at least 2")
    return 1 << ((size-1).bit_length()-1)


def rfc6962_root(leaves:list[bytes]) -> bytes:
    if not leaves:
        return hashlib.sha256(b"").digest()
    if len(leaves)==1:
        return rfc6962_leaf_hash(leaves[0])
    k=_split_point(len(leaves))
    return rfc6962_node_hash(
        rfc6962_root(leaves[:k]),
        rfc6962_root(leaves[k:]),
    )


def rfc6962_inclusion_proof(leaves:list[bytes],index:int) -> list[bytes]:
    if not leaves or index<0 or index>=len(leaves):
        raise ValueError("invalid inclusion proof index")
    if len(leaves)==1:
        return []
    k=_split_point(len(leaves))
    if index<k:
        return rfc6962_inclusion_proof(leaves[:k],index)+[
            rfc6962_root(leaves[k:])
        ]
    return rfc6962_inclusion_proof(leaves[k:],index-k)+[
        rfc6962_root(leaves[:k])
    ]


def verify_rfc6962_inclusion(
    leaf_payload:bytes,
    *,
    index:int,
    tree_size:int,
    proof_hashes:list[str],
    expected_root_hex:str,
) -> bool:
    if index<0 or tree_size<=0 or index>=tree_size:
        return False
    fn=index
    sn=tree_size-1
    value=rfc6962_leaf_hash(leaf_payload)
    try:
        proof=[bytes.fromhex(x) for x in proof_hashes]
    except ValueError:
        return False
    for sibling in proof:
        if fn==sn or fn & 1:
            value=rfc6962_node_hash(sibling,value)
            while fn and not (fn & 1):
                fn >>= 1
                sn >>= 1
        else:
            value=rfc6962_node_hash(value,sibling)
        fn >>= 1
        sn >>= 1
    return value.hex()==expected_root_hex and sn==0


def _public_key(pem_b64:str) -> tuple[Ed25519PublicKey,str,SSlibKey]:
    raw=base64.b64decode(pem_b64.encode("ascii"),validate=True)
    key=serialization.load_pem_public_key(raw)
    if not isinstance(key,Ed25519PublicKey):
        raise ValueError("only Ed25519 verifier keys are supported")
    der=key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fp=hashlib.sha256(der).hexdigest()
    return key,fp,SSlibKey.from_crypto(key,keyid=fp)


def verify_dsse_snapshot(snapshot:dict[str,Any]) -> tuple[list[str],dict]:
    issues=[]
    envelope_dict=copy.deepcopy(snapshot["envelope"])
    envelope=Envelope.from_dict(envelope_dict)
    keys=[]
    for row in snapshot.get("signatures",[]):
        try:
            _,fp,sslib=_public_key(row["public_key_pem_b64"])
            if fp!=row["key_fingerprint_sha256"]:
                issues.append("DSSE_PUBLIC_KEY_FINGERPRINT_MISMATCH")
                continue
            keys.append(sslib)
        except Exception:
            issues.append("DSSE_PUBLIC_KEY_INVALID")
    threshold=int(snapshot["attestation"]["signature_threshold"])
    accepted={}
    try:
        accepted=envelope.verify(keys,threshold)
    except Exception:
        issues.append("DSSE_SIGNATURE_THRESHOLD_NOT_MET")
    return issues,{
        "threshold":threshold,
        "signature_count":len(envelope.signatures),
        "accepted_signer_fingerprints":sorted(accepted),
    }


def _verify_ed25519_detached(
    *,
    payload:bytes,
    signature_b64:str,
    public_key_pem_b64:str,
    expected_fingerprint:str,
) -> bool:
    try:
        _,fp,key=_public_key(public_key_pem_b64)
        if fp!=expected_fingerprint:
            return False
        raw=base64.b64decode(signature_b64.encode("ascii"),validate=True)
        key.verify_signature(Signature(fp,raw.hex()),payload)
        return True
    except Exception:
        return False


def verify_rekor_compatible_receipt(
    row:dict[str,Any],
    *,
    envelope_dict:dict[str,Any],
) -> tuple[list[str],int]:
    issues=[]
    receipt=row["receipt_snapshot"]
    if receipt.get("kind")!="rekor-compatible-v1":
        return ["REKOR_COMPATIBLE_RECEIPT_KIND_INVALID"],0

    envelope_bytes=canonical_json(envelope_dict).encode("utf-8")
    body_sha=hashlib.sha256(envelope_bytes).hexdigest()
    entry=receipt.get("entry",{})
    inclusion=receipt.get("verification",{}).get("inclusionProof",{})
    set_record=receipt.get("verification",{}).get("signedEntryTimestamp",{})
    checkpoint=receipt.get("verification",{}).get("checkpoint",{})

    if entry.get("bodySha256")!=body_sha:
        issues.append("TRANSPARENCY_BODY_SHA_MISMATCH")
    canonical_body=base64.b64decode(
        row["canonicalized_body_b64"].encode("ascii"),validate=True
    )
    if canonical_body!=envelope_bytes:
        issues.append("TRANSPARENCY_CANONICAL_BODY_MISMATCH")

    if not verify_rfc6962_inclusion(
        canonical_body,
        index=int(row["log_index"]),
        tree_size=int(row["tree_size"]),
        proof_hashes=list(row["inclusion_hashes"]),
        expected_root_hex=row["root_hash"],
    ):
        issues.append("REKOR_COMPATIBLE_INCLUSION_INVALID")

    if (
        inclusion.get("logIndex")!=int(row["log_index"])
        or inclusion.get("treeSize")!=int(row["tree_size"])
        or inclusion.get("rootHash")!=row["root_hash"]
        or list(inclusion.get("hashes",[]))!=list(row["inclusion_hashes"])
    ):
        issues.append("REKOR_COMPATIBLE_INCLUSION_RECEIPT_MISMATCH")

    set_payload=canonical_json(entry).encode("utf-8")
    if not _verify_ed25519_detached(
        payload=set_payload,
        signature_b64=set_record.get("signature",""),
        public_key_pem_b64=set_record.get("publicKeyPemB64",""),
        expected_fingerprint=set_record.get("keyFingerprintSha256",""),
    ):
        issues.append("REKOR_COMPATIBLE_SET_INVALID")

    checkpoint_payload={
        "origin":checkpoint.get("origin"),
        "treeSize":checkpoint.get("treeSize"),
        "rootHash":checkpoint.get("rootHash"),
    }
    if (
        checkpoint_payload["treeSize"]!=int(row["tree_size"])
        or checkpoint_payload["rootHash"]!=row["root_hash"]
    ):
        issues.append("REKOR_COMPATIBLE_CHECKPOINT_MISMATCH")
    if not _verify_ed25519_detached(
        payload=canonical_json(checkpoint_payload).encode("utf-8"),
        signature_b64=checkpoint.get("signature",""),
        public_key_pem_b64=checkpoint.get("publicKeyPemB64",""),
        expected_fingerprint=checkpoint.get("keyFingerprintSha256",""),
    ):
        issues.append("REKOR_COMPATIBLE_CHECKPOINT_INVALID")

    integrated=int(entry.get("integratedTime") or 0)
    if integrated<=0:
        issues.append("REKOR_COMPATIBLE_TRUSTED_TIME_INVALID")
    return sorted(set(issues)),1 if not issues else 0


def verify_rfc3161_timestamp(row:dict[str,Any],message:bytes) -> bool:
    if not row.get("timestamp_response_b64") or not row.get("timestamp_chain_pem"):
        return False
    response=decode_timestamp_response(
        base64.b64decode(row["timestamp_response_b64"].encode("ascii"))
    )
    certs=load_pem_x509_certificates(
        row["timestamp_chain_pem"].encode("utf-8")
    )
    if len(certs)<2:
        return False
    builder=VerifierBuilder().tsa_certificate(certs[0]).add_root_certificate(certs[-1])
    for cert in certs[1:-1]:
        builder=builder.add_intermediate_certificate(cert)
    builder.build().verify_message(response,message)
    return True


def verify_exported_bundle(snapshot:dict[str,Any]) -> dict[str,Any]:
    issues,dsse=verify_dsse_snapshot(snapshot)
    envelope_dict=snapshot["envelope"]
    envelope_bytes=canonical_json(envelope_dict).encode("utf-8")
    trusted_times=0
    transparency_verified=False

    for row in snapshot.get("transparency_entries",[]):
        if row.get("provider")=="REKOR_COMPATIBLE":
            local_issues,count=verify_rekor_compatible_receipt(
                row,envelope_dict=envelope_dict
            )
            issues.extend(local_issues)
            trusted_times+=count
            transparency_verified=transparency_verified or not local_issues

    for row in snapshot.get("trusted_timestamps",[]):
        if row.get("timestamp_source")!="RFC3161_TSA":
            continue
        try:
            if verify_rfc3161_timestamp(row,envelope_bytes):
                trusted_times+=1
        except Exception:
            issues.append("RFC3161_TIMESTAMP_INVALID")

    if trusted_times<1:
        issues.append("NO_VERIFIED_TRUSTED_TIME")
    if snapshot.get("transparency_entries") and not transparency_verified:
        local_entries=[
            x for x in snapshot["transparency_entries"]
            if x.get("provider")=="REKOR_COMPATIBLE"
        ]
        if local_entries:
            issues.append("NO_VERIFIED_TRANSPARENCY_ENTRY")

    issues=sorted(set(issues))
    return {
        "verification_status":"PASS" if not issues else "FAIL",
        "issue_codes":issues,
        "dsse":dsse,
        "trusted_time_source_count":trusted_times,
        "transparency_verified":transparency_verified,
        "offline":True,
        "requires_database":False,
        "requires_network":False,
        "requires_private_key":False,
    }
