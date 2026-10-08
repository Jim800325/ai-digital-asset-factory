from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.db import engine


CATEGORIES=(
    "INCIDENT",
    "CIRCUIT",
    "RECOVERY",
    "CERTIFICATION_AUDIT",
    "TRUST_ROOT",
    "HSM_CEREMONY",
    "EXTERNAL_KMS",
    "DSSE",
    "REKOR",
    "PUBLISHER_EXECUTION",
    "CLOUD_CEREMONY",
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _sid(value: Any) -> str | None:
    return None if value is None else str(value)


def _event(
    *,
    category: str,
    kind: str,
    status: str,
    title: str,
    source_table: str,
    source_id: Any,
    occurred_at: Any,
    actor: str | None,
    evidence_sha256: str | None,
    immutable: bool,
    severity: str="INFO",
    parent_id: Any=None,
    details: dict[str,Any] | None=None,
) -> dict[str,Any]:
    source_id_text=_sid(source_id) or "unknown"
    return {
        "id":f"{category.lower()}:{source_id_text}",
        "category":category,
        "kind":kind,
        "status":status,
        "severity":severity,
        "title":title,
        "source_table":source_table,
        "source_id":source_id_text,
        "parent_id":_sid(parent_id),
        "occurred_at":_iso(occurred_at),
        "actor":actor,
        "evidence_sha256":evidence_sha256,
        "immutable":bool(immutable),
        "details":details or {},
    }


def _load_events() -> list[dict[str,Any]]:
    events:list[dict[str,Any]]=[]
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT t.id,t.incident_id,t.event_type,t.source_type,t.source_id,
                 t.event_sha256,t.actor,t.created_at,i.incident_key,
                 i.severity,i.incident_status
          FROM shrimp_bilibili_incident_timeline t
          JOIN shrimp_bilibili_incidents i ON i.id=t.incident_id
          ORDER BY t.created_at DESC,t.id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="INCIDENT",
                kind=r["event_type"],
                status=r["incident_status"],
                title=f'{r["incident_key"]} · {r["event_type"].replace("_"," ")}',
                source_table="shrimp_bilibili_incident_timeline",
                source_id=r["id"],
                parent_id=r["incident_id"],
                occurred_at=r["created_at"],
                actor=r["actor"],
                evidence_sha256=r["event_sha256"],
                immutable=True,
                severity=r["severity"],
                details={
                    "incident_key":r["incident_key"],
                    "source_type":r["source_type"],
                    "source_id":r["source_id"],
                },
            ))

        rows=db.execute(text("""
          SELECT e.id,e.account_id,e.previous_status,e.next_status,
                 e.event_type,e.reason,e.evidence_sha256,e.actor,e.created_at,
                 a.account_key
          FROM shrimp_bilibili_circuit_events e
          JOIN shrimp_bilibili_accounts a ON a.id=e.account_id
          ORDER BY e.created_at DESC,e.id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            status=r["next_status"]
            events.append(_event(
                category="CIRCUIT",
                kind=r["event_type"],
                status=status,
                severity="CRITICAL" if status=="OPEN" else "INFO",
                title=f'{r["account_key"]} · {r["previous_status"]} → {status}',
                source_table="shrimp_bilibili_circuit_events",
                source_id=r["id"],
                parent_id=r["account_id"],
                occurred_at=r["created_at"],
                actor=r["actor"],
                evidence_sha256=r["evidence_sha256"],
                immutable=True,
                details={
                    "account_key":r["account_key"],
                    "previous_status":r["previous_status"],
                    "next_status":status,
                    "reason":r["reason"],
                },
            ))

        rows=db.execute(text("""
          SELECT r.id,r.incident_id,r.request_status,r.decision,
                 r.evidence_sha256,r.requested_by,r.requested_at,
                 r.decision_by,r.decided_at,i.incident_key
          FROM shrimp_bilibili_recovery_approvals r
          JOIN shrimp_bilibili_incidents i ON i.id=r.incident_id
          ORDER BY r.requested_at DESC,r.id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="RECOVERY",
                kind="RECOVERY_APPROVAL",
                status=r["request_status"],
                severity="WARNING" if r["request_status"] in {"PENDING","STALE"} else "INFO",
                title=f'{r["incident_key"]} · Recovery {r["request_status"]}',
                source_table="shrimp_bilibili_recovery_approvals",
                source_id=r["id"],
                parent_id=r["incident_id"],
                occurred_at=r["decided_at"] or r["requested_at"],
                actor=r["decision_by"] or r["requested_by"],
                evidence_sha256=r["evidence_sha256"],
                immutable=False,
                details={
                    "incident_key":r["incident_key"],
                    "decision":r["decision"],
                    "requested_at":_iso(r["requested_at"]),
                    "decided_at":_iso(r["decided_at"]),
                },
            ))

        rows=db.execute(text("""
          SELECT id,run_status,stuck_claim_count,reconciled_count,
                 still_ambiguous_count,escalation_count,circuits_opened,
                 circuits_closed,summary_sha256,started_by,started_at,finished_at
          FROM shrimp_bilibili_recovery_policy_runs
          ORDER BY started_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="RECOVERY",
                kind="RECOVERY_POLICY_RUN",
                status=r["run_status"],
                severity="WARNING" if r["run_status"] in {"PARTIAL","FAILED"} else "INFO",
                title=f'Recovery policy · {r["run_status"]}',
                source_table="shrimp_bilibili_recovery_policy_runs",
                source_id=r["id"],
                occurred_at=r["finished_at"] or r["started_at"],
                actor=r["started_by"],
                evidence_sha256=r["summary_sha256"],
                immutable=False,
                details={
                    "stuck_claims":r["stuck_claim_count"],
                    "reconciled":r["reconciled_count"],
                    "still_ambiguous":r["still_ambiguous_count"],
                    "escalations":r["escalation_count"],
                    "circuits_opened":r["circuits_opened"],
                    "circuits_closed":r["circuits_closed"],
                },
            ))

        rows=db.execute(text("""
          SELECT id,audit_status,certification_count,attestation_count,
                 current_certification_count,issue_codes,audit_snapshot_sha256,
                 audit_sha256,evaluated_by,evaluated_at
          FROM shrimp_bilibili_certification_integrity_audits
          ORDER BY evaluated_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="CERTIFICATION_AUDIT",
                kind="INTEGRITY_AUDIT",
                status=r["audit_status"],
                severity="CRITICAL" if r["audit_status"]=="FAIL" else "INFO",
                title=f'Certification integrity · {r["audit_status"]}',
                source_table="shrimp_bilibili_certification_integrity_audits",
                source_id=r["id"],
                occurred_at=r["evaluated_at"],
                actor=r["evaluated_by"],
                evidence_sha256=r["audit_sha256"],
                immutable=True,
                details={
                    "certification_count":r["certification_count"],
                    "attestation_count":r["attestation_count"],
                    "current_certification_count":r["current_certification_count"],
                    "issue_codes":r["issue_codes"],
                    "snapshot_sha256":r["audit_snapshot_sha256"],
                },
            ))

        rows=db.execute(text("""
          SELECT id,integrity_audit_id,current_certification_id,
                 proof_snapshot_sha256,proof_sha256,generated_by,generated_at
          FROM shrimp_bilibili_certification_audit_proofs
          ORDER BY generated_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="CERTIFICATION_AUDIT",
                kind="AUDIT_PROOF",
                status="GENERATED",
                title="Certification audit proof",
                source_table="shrimp_bilibili_certification_audit_proofs",
                source_id=r["id"],
                parent_id=r["integrity_audit_id"],
                occurred_at=r["generated_at"],
                actor=r["generated_by"],
                evidence_sha256=r["proof_sha256"],
                immutable=True,
                details={
                    "certification_id":_sid(r["current_certification_id"]),
                    "snapshot_sha256":r["proof_snapshot_sha256"],
                },
            ))

        rows=db.execute(text("""
          SELECT id,root_version,previous_root_id,root_threshold,
                 authorized_key_fingerprints,root_sha256,generated_by,generated_at
          FROM shrimp_bilibili_signing_trust_roots
          ORDER BY generated_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="TRUST_ROOT",
                kind="TUF_ROOT",
                status="ACTIVE" if r["previous_root_id"] is None else "ROTATED",
                title=f'TUF Root v{r["root_version"]}',
                source_table="shrimp_bilibili_signing_trust_roots",
                source_id=r["id"],
                parent_id=r["previous_root_id"],
                occurred_at=r["generated_at"],
                actor=r["generated_by"],
                evidence_sha256=r["root_sha256"],
                immutable=True,
                details={
                    "root_version":r["root_version"],
                    "threshold":r["root_threshold"],
                    "authorized_key_count":len(r["authorized_key_fingerprints"] or []),
                },
            ))

        rows=db.execute(text("""
          SELECT id,ceremony_type,trust_root_version,participant_fingerprints,
                 threshold,ceremony_sha256,ceremony_status,executed_by,executed_at
          FROM shrimp_bilibili_root_ceremonies
          ORDER BY executed_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="HSM_CEREMONY",
                kind=r["ceremony_type"],
                status=r["ceremony_status"],
                severity="CRITICAL" if r["ceremony_status"]=="FAILED" else "INFO",
                title=f'HSM ceremony · {r["ceremony_type"]}',
                source_table="shrimp_bilibili_root_ceremonies",
                source_id=r["id"],
                occurred_at=r["executed_at"],
                actor=r["executed_by"],
                evidence_sha256=r["ceremony_sha256"],
                immutable=True,
                details={
                    "trust_root_version":r["trust_root_version"],
                    "threshold":r["threshold"],
                    "participant_count":len(r["participant_fingerprints"] or []),
                },
            ))

        rows=db.execute(text("""
          SELECT e.id,e.provider_id,e.event_type,e.event_sha256,e.recorded_by,
                 e.recorded_at,p.provider_type,p.provider_ref,
                 p.public_key_fingerprint_sha256
          FROM shrimp_bilibili_external_kms_provider_events e
          JOIN shrimp_bilibili_external_kms_providers p ON p.id=e.provider_id
          ORDER BY e.recorded_at DESC,e.id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="EXTERNAL_KMS",
                kind=r["event_type"],
                status=r["event_type"],
                severity="CRITICAL" if r["event_type"]=="UNHEALTHY" else "INFO",
                title=f'{r["provider_type"]} · {r["event_type"]}',
                source_table="shrimp_bilibili_external_kms_provider_events",
                source_id=r["id"],
                parent_id=r["provider_id"],
                occurred_at=r["recorded_at"],
                actor=r["recorded_by"],
                evidence_sha256=r["event_sha256"],
                immutable=True,
                details={
                    "provider_type":r["provider_type"],
                    "provider_ref":r["provider_ref"],
                    "public_key_fingerprint_sha256":r["public_key_fingerprint_sha256"],
                },
            ))

        rows=db.execute(text("""
          SELECT id,trust_root_version,required_provider_threshold,
                 ceremony_sha256,ceremony_status,executed_by,executed_at,
                 provider_signatures
          FROM shrimp_bilibili_cross_kms_root_ceremonies
          ORDER BY executed_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="EXTERNAL_KMS",
                kind="CROSS_KMS_ROOT_CEREMONY",
                status=r["ceremony_status"],
                severity="CRITICAL" if r["ceremony_status"]=="FAILED" else "INFO",
                title="Cross-KMS root ceremony",
                source_table="shrimp_bilibili_cross_kms_root_ceremonies",
                source_id=r["id"],
                occurred_at=r["executed_at"],
                actor=r["executed_by"],
                evidence_sha256=r["ceremony_sha256"],
                immutable=True,
                details={
                    "trust_root_version":r["trust_root_version"],
                    "required_threshold":r["required_provider_threshold"],
                    "signature_count":len(r["provider_signatures"] or []),
                },
            ))

        rows=db.execute(text("""
          SELECT id,subject_type,subject_id,statement_sha256,envelope_sha256,
                 signature_threshold,signer_fingerprints,created_by,created_at
          FROM shrimp_bilibili_dsse_attestations
          ORDER BY created_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="DSSE",
                kind="DSSE_ATTESTATION",
                status="ATTESTED",
                title=f'DSSE · {r["subject_type"]}',
                source_table="shrimp_bilibili_dsse_attestations",
                source_id=r["id"],
                parent_id=r["subject_id"],
                occurred_at=r["created_at"],
                actor=r["created_by"],
                evidence_sha256=r["envelope_sha256"],
                immutable=True,
                details={
                    "subject_type":r["subject_type"],
                    "subject_id":r["subject_id"],
                    "statement_sha256":r["statement_sha256"],
                    "signature_threshold":r["signature_threshold"],
                    "signer_count":len(r["signer_fingerprints"] or []),
                },
            ))

        rows=db.execute(text("""
          SELECT id,attestation_id,provider,entry_uuid,log_index,tree_size,
                 root_hash,integrated_time,receipt_sha256,recorded_by,recorded_at
          FROM shrimp_bilibili_transparency_entries
          ORDER BY recorded_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="REKOR",
                kind="TRANSPARENCY_ENTRY",
                status="INCLUDED",
                title=f'{r["provider"]} · log #{r["log_index"]}',
                source_table="shrimp_bilibili_transparency_entries",
                source_id=r["id"],
                parent_id=r["attestation_id"],
                occurred_at=r["integrated_time"] or r["recorded_at"],
                actor=r["recorded_by"],
                evidence_sha256=r["receipt_sha256"],
                immutable=True,
                details={
                    "provider":r["provider"],
                    "entry_uuid":r["entry_uuid"],
                    "log_index":r["log_index"],
                    "tree_size":r["tree_size"],
                    "root_hash":r["root_hash"],
                },
            ))

        rows=db.execute(text("""
          SELECT e.id,e.execution_id,e.event_type,e.previous_status,e.next_status,
                 e.actor,e.provider_result_sha256,e.created_at,
                 x.execution_sha256,x.platform,x.target_key
          FROM shrimp_animation_publish_execution_events e
          JOIN shrimp_animation_publish_executions x ON x.id=e.execution_id
          ORDER BY e.created_at DESC,e.id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            status=r["next_status"] or r["event_type"]
            events.append(_event(
                category="PUBLISHER_EXECUTION",
                kind=r["event_type"],
                status=status,
                severity="CRITICAL" if "FAILED" in status or "UNKNOWN" in status else "INFO",
                title=f'{r["platform"]} · {r["target_key"]} · {r["event_type"]}',
                source_table="shrimp_animation_publish_execution_events",
                source_id=r["id"],
                parent_id=r["execution_id"],
                occurred_at=r["created_at"],
                actor=r["actor"],
                evidence_sha256=r["provider_result_sha256"] or r["execution_sha256"],
                immutable=True,
                details={
                    "platform":r["platform"],
                    "target_key":r["target_key"],
                    "previous_status":r["previous_status"],
                    "next_status":r["next_status"],
                    "execution_sha256":r["execution_sha256"],
                },
            ))

        rows=db.execute(text("""
          SELECT id,provider_type,provider_ref,acceptance_sha256,
                 acceptance_status,live_signature_verified,cleanup_verified,
                 post_cleanup_sign_blocked,external_write_count,executed_by,
                 executed_at
          FROM shrimp_bilibili_live_cloud_kms_acceptance_runs
          ORDER BY executed_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="CLOUD_CEREMONY",
                kind="LIVE_CLOUD_KMS_ACCEPTANCE",
                status=r["acceptance_status"],
                severity="CRITICAL" if r["acceptance_status"]=="FAILED" else "INFO",
                title=f'{r["provider_type"]} · Live KMS acceptance',
                source_table="shrimp_bilibili_live_cloud_kms_acceptance_runs",
                source_id=r["id"],
                occurred_at=r["executed_at"],
                actor=r["executed_by"],
                evidence_sha256=r["acceptance_sha256"],
                immutable=True,
                details={
                    "provider_type":r["provider_type"],
                    "provider_ref":r["provider_ref"],
                    "live_signature_verified":r["live_signature_verified"],
                    "cleanup_verified":r["cleanup_verified"],
                    "post_cleanup_sign_blocked":r["post_cleanup_sign_blocked"],
                    "external_write_count":r["external_write_count"],
                },
            ))

        rows=db.execute(text("""
          SELECT id,primary_provider_ref,fallback_provider_ref,outage_sha256,
                 failover_status,primary_failure_observed,
                 fallback_signature_verified,external_write_count,
                 executed_by,executed_at
          FROM shrimp_bilibili_live_cloud_kms_outage_drills
          ORDER BY executed_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="CLOUD_CEREMONY",
                kind="OUTAGE_FAILOVER_DRILL",
                status=r["failover_status"],
                severity="CRITICAL" if r["failover_status"]=="FAILED" else "INFO",
                title="Cloud KMS outage / failover drill",
                source_table="shrimp_bilibili_live_cloud_kms_outage_drills",
                source_id=r["id"],
                occurred_at=r["executed_at"],
                actor=r["executed_by"],
                evidence_sha256=r["outage_sha256"],
                immutable=True,
                details={
                    "primary_provider_ref":r["primary_provider_ref"],
                    "fallback_provider_ref":r["fallback_provider_ref"],
                    "primary_failure_observed":r["primary_failure_observed"],
                    "fallback_signature_verified":r["fallback_signature_verified"],
                    "external_write_count":r["external_write_count"],
                },
            ))

        rows=db.execute(text("""
          SELECT id,trust_root_version,required_provider_threshold,
                 live_provider_refs,ceremony_sha256,ceremony_status,
                 external_write_count,executed_by,executed_at
          FROM shrimp_bilibili_live_cross_cloud_ceremonies
          ORDER BY executed_at DESC,id DESC
          LIMIT 500
        """)).mappings().all()
        for r in rows:
            events.append(_event(
                category="CLOUD_CEREMONY",
                kind="LIVE_CROSS_CLOUD_CEREMONY",
                status=r["ceremony_status"],
                severity="CRITICAL" if r["ceremony_status"]=="FAILED" else "INFO",
                title="Live cross-cloud root ceremony",
                source_table="shrimp_bilibili_live_cross_cloud_ceremonies",
                source_id=r["id"],
                occurred_at=r["executed_at"],
                actor=r["executed_by"],
                evidence_sha256=r["ceremony_sha256"],
                immutable=True,
                details={
                    "trust_root_version":r["trust_root_version"],
                    "required_threshold":r["required_provider_threshold"],
                    "provider_count":len(r["live_provider_refs"] or []),
                    "external_write_count":r["external_write_count"],
                },
            ))

    events.sort(
        key=lambda item:(item.get("occurred_at") or "",item["id"]),
        reverse=True,
    )
    return events


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    if parsed.tzinfo is None:
        parsed=parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _filter_events(
    events: list[dict[str,Any]],
    *,
    category: str | None=None,
    status: str | None=None,
    q: str | None=None,
    after: str | None=None,
    before: str | None=None,
) -> list[dict[str,Any]]:
    categories={
        value.strip().upper()
        for value in (category or "").split(",")
        if value.strip()
    }
    statuses={
        value.strip().upper()
        for value in (status or "").split(",")
        if value.strip()
    }
    query=(q or "").strip().casefold()
    after_dt=_parse_time(after)
    before_dt=_parse_time(before)
    result=[]
    for item in events:
        if categories and item["category"] not in categories:
            continue
        if statuses and str(item["status"]).upper() not in statuses:
            continue
        occurred=_parse_time(item.get("occurred_at"))
        if after_dt and (occurred is None or occurred<after_dt):
            continue
        if before_dt and (occurred is None or occurred>before_dt):
            continue
        if query:
            haystack=json.dumps(
                {
                    "title":item["title"],
                    "kind":item["kind"],
                    "status":item["status"],
                    "source_id":item["source_id"],
                    "parent_id":item["parent_id"],
                    "actor":item["actor"],
                    "evidence_sha256":item["evidence_sha256"],
                    "details":item["details"],
                },
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).casefold()
            if query not in haystack:
                continue
        result.append(item)
    return result


def audit_evidence_explorer(
    *,
    category: str | None=None,
    status: str | None=None,
    q: str | None=None,
    after: str | None=None,
    before: str | None=None,
    limit: int=200,
    offset: int=0,
) -> dict[str,Any]:
    all_events=_load_events()
    filtered=_filter_events(
        all_events,
        category=category,
        status=status,
        q=q,
        after=after,
        before=before,
    )
    safe_limit=max(1,min(int(limit),500))
    safe_offset=max(0,int(offset))
    page=filtered[safe_offset:safe_offset+safe_limit]
    by_category=Counter(item["category"] for item in filtered)
    by_status=Counter(str(item["status"]) for item in filtered)
    return {
        "mode":"READ_ONLY_AUDIT_EVIDENCE_EXPLORER",
        "schema_version":"unified-audit-evidence-v1",
        "categories":list(CATEGORIES),
        "events":page,
        "summary":{
            "total":len(filtered),
            "returned":len(page),
            "immutable_events":sum(1 for item in filtered if item["immutable"]),
            "with_evidence_sha256":sum(
                1 for item in filtered if item.get("evidence_sha256")
            ),
            "by_category":dict(sorted(by_category.items())),
            "by_status":dict(sorted(by_status.items())),
        },
        "pagination":{
            "offset":safe_offset,
            "limit":safe_limit,
            "has_more":safe_offset+safe_limit<len(filtered),
        },
        "filters":{
            "category":category,
            "status":status,
            "q":q,
            "after":after,
            "before":before,
        },
        "safety":{
            "read_only":True,
            "raw_secret_snapshots_exposed":False,
            "credentials_exposed":False,
            "private_keys_exposed":False,
            "production_writes":False,
            "provider_writes":False,
        },
        "secrets_redacted":True,
    }


def export_audit_evidence(
    *,
    format: str="json",
    category: str | None=None,
    status: str | None=None,
    q: str | None=None,
    after: str | None=None,
    before: str | None=None,
) -> tuple[str,str,str]:
    result=audit_evidence_explorer(
        category=category,
        status=status,
        q=q,
        after=after,
        before=before,
        limit=500,
        offset=0,
    )
    filtered=_filter_events(
        _load_events(),
        category=category,
        status=status,
        q=q,
        after=after,
        before=before,
    )
    fmt=(format or "json").strip().lower()
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if fmt=="json":
        payload={
            "schema_version":result["schema_version"],
            "exported_at":datetime.now(timezone.utc).isoformat(),
            "filters":result["filters"],
            "summary":{
                **result["summary"],
                "returned":len(filtered),
            },
            "events":filtered,
            "safety":result["safety"],
            "secrets_redacted":True,
        }
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                default=str,
            ),
            "application/json; charset=utf-8",
            f"shrimp-audit-evidence-{stamp}.json",
        )
    if fmt=="csv":
        output=io.StringIO(newline="")
        fieldnames=[
            "occurred_at","category","kind","status","severity","title",
            "source_table","source_id","parent_id","actor",
            "evidence_sha256","immutable","details",
        ]
        writer=csv.DictWriter(output,fieldnames=fieldnames)
        writer.writeheader()
        for item in filtered:
            row={key:item.get(key) for key in fieldnames}
            row["details"]=json.dumps(
                item.get("details") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",",":"),
                default=str,
            )
            writer.writerow(row)
        return (
            output.getvalue(),
            "text/csv; charset=utf-8",
            f"shrimp-audit-evidence-{stamp}.csv",
        )
    raise ValueError("format must be json or csv")
