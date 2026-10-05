from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

from securesystemslib.signer import Signature
from sqlalchemy import text

from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_post_restore_certification import _ser,_sha
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    _build_tuf_root,
    _get_key_by_fingerprint,
    _sslib_key,
    list_signing_keys,
    list_trust_roots,
    verify_bundle_with_key_registry,
    verify_trust_root_chain,
)


def create_root_transition_plan(
    *,
    candidate_fingerprints:list[str],
    candidate_threshold:int,
    transition_type:str,
    actor:str,
) -> dict:
    roots=list_trust_roots(limit=1)
    if not roots:
        raise RuntimeError("Current TUF root is required")
    previous=roots[0]
    keys=[]
    for fp in dict.fromkeys(candidate_fingerprints):
        key=_get_key_by_fingerprint(fp)
        if key is None:
            raise LookupError(f"Signing key not registered: {fp}")
        keys.append(key)
    snapshot=_build_tuf_root(
        keys,
        version=int(previous["root_version"])+1,
        threshold=int(candidate_threshold),
    )
    sha=_sha(snapshot)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_root_transition_plans(
            previous_root_id,candidate_root_snapshot,candidate_root_sha256,
            previous_threshold,candidate_threshold,required_human_approvals,
            transition_type,created_by)
          VALUES(
            :previous_id,CAST(:snapshot AS jsonb),:sha,:previous_threshold,
            :candidate_threshold,2,:transition_type,:actor)
          RETURNING *
        """),{
          "previous_id":previous["id"],"snapshot":canonical_json(snapshot),
          "sha":sha,"previous_threshold":previous["root_threshold"],
          "candidate_threshold":candidate_threshold,
          "transition_type":transition_type,"actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def get_transition_plan(plan_id:UUID) -> dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_root_transition_plans WHERE id=:id
        """),{"id":plan_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Root transition plan not found")
    return _ser(row)


def add_transition_signature(
    plan_id:UUID,*,fingerprint:str,signature_b64:str,actor:str
) -> dict:
    plan=get_transition_plan(plan_id)
    key=_get_key_by_fingerprint(fingerprint)
    if key is None:
        raise LookupError("Signing key not registered")
    try:
        raw=base64.b64decode(signature_b64.encode("ascii"),validate=True)
        sig=Signature(fingerprint,raw.hex())
        _sslib_key(key).verify_signature(
            sig,plan["candidate_root_sha256"].encode("ascii")
        )
    except Exception as exc:
        raise ValueError("Invalid root transition signature") from exc
    previous=get_previous_root(plan)
    previous_set=set(previous["authorized_key_fingerprints"])
    candidate_set=set(plan["candidate_root_snapshot"]["roles"]["root"]["keyids"])
    if fingerprint in previous_set and fingerprint in candidate_set:
        scope="BOTH"
    elif fingerprint in previous_set:
        scope="PREVIOUS_ROOT"
    elif fingerprint in candidate_set:
        scope="CANDIDATE_ROOT"
    else:
        raise ValueError("Signer is not authorized by previous or candidate root")
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_root_transition_signatures(
            plan_id,key_fingerprint_sha256,signature_b64,signer_scope,signed_by)
          VALUES(:plan,:fp,:sig,:scope,:actor)
          RETURNING *
        """),{
          "plan":plan_id,"fp":fingerprint,"sig":signature_b64,
          "scope":scope,"actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def get_previous_root(plan:dict) -> dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_signing_trust_roots WHERE id=:id
        """),{"id":plan["previous_root_id"]}).mappings().one()
    return _ser(row)


def transition_signature_status(plan_id:UUID) -> dict:
    plan=get_transition_plan(plan_id)
    previous=get_previous_root(plan)
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_root_transition_signatures
          WHERE plan_id=:id
        """),{"id":plan_id}).mappings().all()
    fps={r["key_fingerprint_sha256"] for r in rows}
    previous_set=set(previous["authorized_key_fingerprints"])
    candidate_set=set(plan["candidate_root_snapshot"]["roles"]["root"]["keyids"])
    previous_count=len(fps & previous_set)
    candidate_count=len(fps & candidate_set)
    return {
      "previous_signature_count":previous_count,
      "previous_threshold":plan["previous_threshold"],
      "candidate_signature_count":candidate_count,
      "candidate_threshold":plan["candidate_threshold"],
      "previous_threshold_met":previous_count>=plan["previous_threshold"],
      "candidate_threshold_met":candidate_count>=plan["candidate_threshold"],
      "cryptographic_transition_authorized":
        previous_count>=plan["previous_threshold"]
        and candidate_count>=plan["candidate_threshold"],
      "unique_signer_count":len(fps),
    }


def decide_transition(
    plan_id:UUID,*,decision:str,reason:str,approver:str
) -> dict:
    if decision not in ("APPROVE","REJECT"):
        raise ValueError("Invalid transition decision")
    material={
      "plan_id":str(plan_id),"decision":decision,"reason":reason,
      "approver":approver,
    }
    sha=_sha(material)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_root_transition_approvals(
            plan_id,approver,decision,reason,approval_sha256)
          VALUES(:plan,:approver,:decision,:reason,:sha)
          RETURNING *
        """),{
          "plan":plan_id,"approver":approver[:200],"decision":decision,
          "reason":reason[:1000],"sha":sha,
        }).mappings().one()
    return _ser(row)


def transition_approval_status(plan_id:UUID) -> dict:
    plan=get_transition_plan(plan_id)
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_root_transition_approvals
          WHERE plan_id=:id ORDER BY recorded_at,id
        """),{"id":plan_id}).mappings().all()
    approvals={r["approver"] for r in rows if r["decision"]=="APPROVE"}
    rejected=any(r["decision"]=="REJECT" for r in rows)
    return {
      "approval_count":len(approvals),
      "required_approvals":plan["required_human_approvals"],
      "distinct_approvers":len(approvals),
      "rejected":rejected,
      "dual_control_authorized":
        not rejected and len(approvals)>=plan["required_human_approvals"],
    }


def apply_root_transition(plan_id:UUID,*,actor:str) -> dict:
    plan=get_transition_plan(plan_id)
    sig=transition_signature_status(plan_id)
    human=transition_approval_status(plan_id)
    if not sig["cryptographic_transition_authorized"]:
        raise RuntimeError("Root transition signature thresholds are not met")
    if not human["dual_control_authorized"]:
        raise RuntimeError("Root transition dual-control approvals are not met")
    with engine.begin() as db:
        existing=db.execute(text("""
          SELECT * FROM shrimp_bilibili_root_transition_applications
          WHERE plan_id=:id
        """),{"id":plan_id}).mappings().one_or_none()
        if existing is not None:
            return _ser(existing)
        root=db.execute(text("""
          INSERT INTO shrimp_bilibili_signing_trust_roots(
            root_version,previous_root_id,root_threshold,
            authorized_key_fingerprints,root_snapshot,root_sha256,
            transition_type,generated_by)
          VALUES(
            :version,:previous_id,:threshold,CAST(:fps AS jsonb),
            CAST(:snapshot AS jsonb),:sha,:transition,:actor)
          RETURNING *
        """),{
          "version":plan["candidate_root_snapshot"]["version"],
          "previous_id":plan["previous_root_id"],
          "threshold":plan["candidate_threshold"],
          "fps":canonical_json(
            plan["candidate_root_snapshot"]["roles"]["root"]["keyids"]
          ),
          "snapshot":canonical_json(plan["candidate_root_snapshot"]),
          "sha":plan["candidate_root_sha256"],
          "transition":plan["transition_type"],
          "actor":actor[:200],
        }).mappings().one()
        snapshot={
          "plan_id":str(plan_id),"resulting_root_id":str(root["id"]),
          "resulting_root_version":root["root_version"],
          "cryptographic_threshold":sig,"dual_control":human,
          "provider_writes":False,
        }
        app_sha=_sha(snapshot)
        app=db.execute(text("""
          INSERT INTO shrimp_bilibili_root_transition_applications(
            plan_id,resulting_root_id,application_status,
            application_snapshot,application_sha256,applied_by)
          VALUES(
            :plan,:root,'APPLIED',CAST(:snapshot AS jsonb),:sha,:actor)
          RETURNING *
        """),{
          "plan":plan_id,"root":root["id"],"snapshot":canonical_json(snapshot),
          "sha":app_sha,"actor":actor[:200],
        }).mappings().one()
    return _ser(app)


def multisigner_dashboard() -> dict[str,Any]:
    with engine.connect() as db:
        plans=db.execute(text("""
          SELECT * FROM shrimp_bilibili_root_transition_plans
          ORDER BY created_at DESC LIMIT 100
        """)).mappings().all()
    return {
      "plans":[_ser(x) for x in plans],
      "threshold_engine":"securesystemslib",
      "trust_model":"python-tuf-root-continuity",
      "dual_control_required":True,
      "minimum_human_approvals":2,
      "automatic_provider_writes":False,
    }


def run_key_compromise_recovery_drill(
    *,
    compromised_fingerprint:str,
    affected_bundle_ids:list[UUID],
    actor:str,
) -> dict:
    roots=list_trust_roots(limit=1)
    if not roots:
        raise RuntimeError("Recovery drill requires a current TUF root")
    current=roots[0]
    issues=[]
    if compromised_fingerprint in set(current["authorized_key_fingerprints"]):
        issues.append("COMPROMISED_KEY_STILL_AUTHORIZED")
    chain=verify_trust_root_chain()
    if chain["verification_status"]!="PASS":
        issues.append("TRUST_ROOT_CHAIN_INVALID")
    bundle_results=[]
    for bundle_id in affected_bundle_ids:
        result=verify_bundle_with_key_registry(bundle_id)
        audit_result=dict(result)
        if hasattr(audit_result.get("signed_at"),"isoformat"):
            audit_result["signed_at"]=audit_result["signed_at"].isoformat()
        bundle_results.append(audit_result)
        if result["verification_status"]!="FAIL":
            issues.append("AFFECTED_BUNDLE_STILL_VALID")
        if "KEY_REVOKED_AT_SIGNING_TIME" not in result["issue_codes"]:
            issues.append("AFFECTED_BUNDLE_MISSING_COMPROMISE_REVOCATION")
    issues=sorted(set(issues))
    snapshot={
        "schema_version":"shrimp-bilibili-key-compromise-recovery-drill-v0.1",
        "compromised_key_fingerprint_sha256":compromised_fingerprint,
        "current_root_version":current["root_version"],
        "affected_bundle_ids":[str(x) for x in affected_bundle_ids],
        "bundle_results":bundle_results,
        "trust_root_chain":chain,
        "issue_codes":issues,
        "production_writes":False,
        "automatic_provider_writes":False,
    }
    drill_sha=_sha(snapshot)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_key_compromise_recovery_drills(
            compromised_key_fingerprint_sha256,recovery_root_version,
            drill_status,issue_codes,drill_snapshot,drill_sha256,executed_by)
          VALUES(
            :fp,:version,:status,CAST(:issues AS jsonb),
            CAST(:snapshot AS jsonb),:sha,:actor)
          RETURNING *
        """),{
            "fp":compromised_fingerprint,
            "version":current["root_version"],
            "status":"PASSED" if not issues else "FAILED",
            "issues":canonical_json(issues),
            "snapshot":canonical_json(snapshot),
            "sha":drill_sha,
            "actor":actor[:200],
        }).mappings().one()
    result=_ser(row)
    if issues:
        raise RuntimeError("Key compromise recovery drill failed: "+",".join(issues))
    return result


def list_key_compromise_recovery_drills(*,limit:int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_key_compromise_recovery_drills
          ORDER BY executed_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]
