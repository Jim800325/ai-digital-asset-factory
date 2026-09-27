import hashlib
import json

from sqlalchemy import text

from app.db import engine

GENERATOR_VERSION = "build-proposal-v0.3-deterministic"

ARTIFACT_TYPE = {
    "DATASET_API":"API_SERVICE",
    "INTELLIGENCE_REPORT":"REPORT_PIPELINE",
    "MICRO_SAAS_TOOL":"WEB_APP",
    "TEMPLATE_WORKFLOW":"TEMPLATE_PACKAGE",
    "CONTENT_IP":"CONTENT_PACKAGE",
}

STACK = {
    "DATASET_API":["Python","FastAPI","PostgreSQL","pytest"],
    "INTELLIGENCE_REPORT":["Python","PostgreSQL","Markdown","pytest"],
    "MICRO_SAAS_TOOL":["Python","FastAPI","PostgreSQL","HTML/CSS/JS","pytest"],
    "TEMPLATE_WORKFLOW":["Markdown","JSON/YAML","pytest"],
    "CONTENT_IP":["Markdown","Python","pytest"],
}

def _proposal_payload(opportunity: dict, report: dict, validation: dict) -> dict:
    source_snapshot={
        "opportunity_id":str(opportunity["id"]),
        "asset_type":opportunity["asset_type"],
        "score":float(opportunity["score"]),
        "build_readiness":opportunity["build_readiness"],
        "research_validation_score":float(opportunity["research_validation_score"]),
        "report_id":str(report["id"]),
        "report_status":report["report_status"],
        "report_generator_version":report["generator_version"],
        "validation_id":str(validation["id"]),
        "validation_status":validation["validation_status"],
        "validation_gate_passed":bool(validation["validation_gate_passed"]),
        "validation_completeness_score":float(validation["completeness_score"]),
        "validator_version":validation["validator_version"],
        "buyer_status":validation["buyer_status"],
        "competitors_status":validation["competitors_status"],
        "pricing_status":validation["pricing_status"],
        "willingness_to_pay_status":validation["willingness_to_pay_status"],
        "market_gap_status":validation["market_gap_status"],
    }
    source_fingerprint=hashlib.sha256(
        json.dumps(source_snapshot,sort_keys=True,ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    artifact_type=ARTIFACT_TYPE.get(opportunity["asset_type"],"DIGITAL_ARTIFACT")
    objective=(
        f"Produce a sandbox-only prototype for {opportunity['title']} "
        f"as {artifact_type}. The output is for technical evaluation only."
    )
    scope={
        "problem":report["problem"],
        "buyer":report["buyer"],
        "monetization":report["monetization"],
        "build_complexity":report["build_complexity"],
        "why_now":report["why_now"],
    }
    success_criteria=[
        "Prototype runs in an isolated sandbox without production credentials.",
        "Automated tests cover the primary happy path and at least one failure path.",
        "README documents setup, assumptions, limitations, and test commands.",
        "No deployment, publishing, payment, outreach, or account registration occurs.",
        "All generated text and files use UTF-8.",
    ]
    constraints=[
        "No production deployment.",
        "No autonomous Git push.",
        "No paid API/resource creation.",
        "No email, social posting, or external outreach.",
        "No production server modification.",
        "No access to secrets beyond explicitly scoped sandbox credentials.",
        "No execution outside an isolated sandbox.",
    ]
    sandbox_policy={
        "network":"DENY_BY_DEFAULT",
        "filesystem":"WORKSPACE_ONLY",
        "production_credentials":"DENY",
        "deployment":"DENY",
        "external_side_effects":"DENY",
        "human_release_required":True,
    }
    return {
        "title":f"Prototype: {opportunity['title']}",
        "objective":objective,
        "artifact_type":artifact_type,
        "scope":scope,
        "success_criteria":success_criteria,
        "constraints":constraints,
        "sandbox_policy":sandbox_policy,
        "proposed_stack":STACK.get(opportunity["asset_type"],["Python","pytest"]),
        "source_snapshot":source_snapshot,
        "source_fingerprint":source_fingerprint,
    }

def _current_source_state(db, opportunity_id):
    opportunity=db.execute(text("""
      SELECT id,title,asset_type,score,build_readiness,research_validation_score
      FROM digital_asset_opportunities
      WHERE id=CAST(:id AS uuid)
    """),{"id":opportunity_id}).mappings().one_or_none()
    if not opportunity:
        return None,None,None

    report=db.execute(text("""
      SELECT id,report_status,problem,buyer,monetization,build_complexity,why_now,
             generator_version
      FROM research_reports
      WHERE opportunity_id=CAST(:id AS uuid)
    """),{"id":opportunity_id}).mappings().one_or_none()

    validation=db.execute(text("""
      SELECT id,validation_status,buyer_status,competitors_status,pricing_status,
             willingness_to_pay_status,market_gap_status,completeness_score,
             validation_gate_passed,validator_version
      FROM research_validations
      WHERE opportunity_id=CAST(:id AS uuid)
    """),{"id":opportunity_id}).mappings().one_or_none()
    return opportunity,report,validation

def _is_build_ready(opportunity, report, validation) -> bool:
    return bool(
        opportunity
        and report
        and validation
        and opportunity["build_readiness"]=="BUILD_READY"
        and report["report_status"]=="GENERATED"
        and validation["validation_status"]=="CURRENT"
        and validation["validation_gate_passed"]
    )

def mark_build_proposal_stale(opportunity_id) -> None:
    with engine.begin() as db:
        db.execute(text("""
          UPDATE build_proposals
          SET proposal_status='STALE',updated_at=now()
          WHERE opportunity_id=CAST(:id AS uuid)
            AND proposal_status<>'STALE'
        """),{"id":opportunity_id})
        db.execute(text("""
          UPDATE digital_asset_opportunities
          SET build_proposal_status='STALE'
          WHERE id=CAST(:id AS uuid)
            AND build_proposal_status NOT IN ('NONE','STALE')
        """),{"id":opportunity_id})

def ensure_build_proposal(opportunity_id):
    with engine.begin() as db:
        opportunity,report,validation=_current_source_state(db,opportunity_id)
        if not _is_build_ready(opportunity,report,validation):
            db.execute(text("""
              UPDATE build_proposals
              SET proposal_status='STALE',updated_at=now()
              WHERE opportunity_id=CAST(:id AS uuid)
                AND proposal_status<>'STALE'
            """),{"id":opportunity_id})
            if opportunity:
                db.execute(text("""
                  UPDATE digital_asset_opportunities
                  SET build_proposal_status=CASE
                    WHEN build_proposal_status='NONE' THEN 'NONE'
                    ELSE 'STALE'
                  END
                  WHERE id=CAST(:id AS uuid)
                """),{"id":opportunity_id})
            return None

        payload=_proposal_payload(dict(opportunity),dict(report),dict(validation))
        existing=db.execute(text("""
          SELECT id,revision,proposal_status,source_fingerprint,generator_version
          FROM build_proposals
          WHERE opportunity_id=CAST(:id AS uuid)
        """),{"id":opportunity_id}).mappings().one_or_none()

        if (
            existing
            and existing["source_fingerprint"]==payload["source_fingerprint"]
            and existing["generator_version"]==GENERATOR_VERSION
            and existing["proposal_status"]!="STALE"
        ):
            return {
                "proposal_id":str(existing["id"]),
                "opportunity_id":str(opportunity_id),
                "revision":existing["revision"],
                "proposal_status":existing["proposal_status"],
                "created":False,
            }

        revision=(int(existing["revision"])+1) if existing else 1
        params={
            "id":opportunity_id,
            "revision":revision,
            "title":payload["title"],
            "objective":payload["objective"],
            "artifact_type":payload["artifact_type"],
            "scope":json.dumps(payload["scope"],ensure_ascii=False),
            "success":json.dumps(payload["success_criteria"],ensure_ascii=False),
            "constraints":json.dumps(payload["constraints"],ensure_ascii=False),
            "sandbox":json.dumps(payload["sandbox_policy"],ensure_ascii=False),
            "stack":json.dumps(payload["proposed_stack"],ensure_ascii=False),
            "snapshot":json.dumps(payload["source_snapshot"],ensure_ascii=False),
            "fingerprint":payload["source_fingerprint"],
            "generator":GENERATOR_VERSION,
        }

        if existing:
            proposal_id=db.execute(text("""
              UPDATE build_proposals
              SET revision=:revision,
                  proposal_status='PENDING_APPROVAL',
                  title=:title,
                  objective=:objective,
                  artifact_type=:artifact_type,
                  scope=CAST(:scope AS jsonb),
                  success_criteria=CAST(:success AS jsonb),
                  constraints=CAST(:constraints AS jsonb),
                  sandbox_policy=CAST(:sandbox AS jsonb),
                  proposed_stack=CAST(:stack AS jsonb),
                  source_snapshot=CAST(:snapshot AS jsonb),
                  source_fingerprint=:fingerprint,
                  generator_version=:generator,
                  execution_enabled=false,
                  approved_at=NULL,
                  rejected_at=NULL,
                  updated_at=now()
              WHERE opportunity_id=CAST(:id AS uuid)
              RETURNING id
            """),params).scalar_one()
        else:
            proposal_id=db.execute(text("""
              INSERT INTO build_proposals(
                opportunity_id,revision,proposal_status,title,objective,artifact_type,
                scope,success_criteria,constraints,sandbox_policy,proposed_stack,
                source_snapshot,source_fingerprint,generator_version)
              VALUES(
                CAST(:id AS uuid),:revision,'PENDING_APPROVAL',:title,:objective,:artifact_type,
                CAST(:scope AS jsonb),CAST(:success AS jsonb),CAST(:constraints AS jsonb),
                CAST(:sandbox AS jsonb),CAST(:stack AS jsonb),CAST(:snapshot AS jsonb),
                :fingerprint,:generator)
              RETURNING id
            """),params).scalar_one()

        db.execute(text("""
          UPDATE digital_asset_opportunities
          SET build_proposal_status='PENDING_APPROVAL'
          WHERE id=CAST(:id AS uuid)
        """),{"id":opportunity_id})

    return {
        "proposal_id":str(proposal_id),
        "opportunity_id":str(opportunity_id),
        "revision":revision,
        "proposal_status":"PENDING_APPROVAL",
        "created":True,
    }

def decide_build_proposal(proposal_id, *, decision: str, reason: str, actor: str = "human"):
    normalized=decision.upper().strip()
    if normalized not in {"APPROVE","REJECT"}:
        raise ValueError("decision must be APPROVE or REJECT")

    with engine.begin() as db:
        proposal=db.execute(text("""
          SELECT id,opportunity_id,revision,proposal_status,source_fingerprint
          FROM build_proposals
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """),{"id":proposal_id}).mappings().one_or_none()
        if proposal is None:
            raise LookupError("Build proposal not found")
        if proposal["proposal_status"]!="PENDING_APPROVAL":
            raise RuntimeError("Only PENDING_APPROVAL proposals can be decided")

        opportunity,report,validation=_current_source_state(db,proposal["opportunity_id"])
        if not _is_build_ready(opportunity,report,validation):
            db.execute(text("""
              UPDATE build_proposals
              SET proposal_status='STALE',updated_at=now()
              WHERE id=:id
            """),{"id":proposal["id"]})
            db.execute(text("""
              UPDATE digital_asset_opportunities
              SET build_proposal_status='STALE'
              WHERE id=:id
            """),{"id":proposal["opportunity_id"]})
            raise RuntimeError("Proposal became stale because BUILD_READY is no longer current")

        current=_proposal_payload(dict(opportunity),dict(report),dict(validation))
        if current["source_fingerprint"]!=proposal["source_fingerprint"]:
            raise RuntimeError("Proposal source state changed; regenerate before approval")

        status="APPROVED" if normalized=="APPROVE" else "REJECTED"
        timestamp_column="approved_at" if normalized=="APPROVE" else "rejected_at"
        db.execute(text(f"""
          UPDATE build_proposals
          SET proposal_status=:status,
              {timestamp_column}=now(),
              execution_enabled=false,
              updated_at=now()
          WHERE id=:id
        """),{"status":status,"id":proposal["id"]})
        db.execute(text("""
          UPDATE digital_asset_opportunities
          SET build_proposal_status=:status
          WHERE id=:id
        """),{"status":status,"id":proposal["opportunity_id"]})
        decision_id=db.execute(text("""
          INSERT INTO build_proposal_decisions(
            proposal_id,proposal_revision,decision,reason,actor)
          VALUES(:proposal_id,:revision,:decision,:reason,:actor)
          RETURNING id
        """),{
            "proposal_id":proposal["id"],
            "revision":proposal["revision"],
            "decision":normalized,
            "reason":reason[:4000],
            "actor":actor[:200],
        }).scalar_one()

    return {
        "proposal_id":str(proposal["id"]),
        "opportunity_id":str(proposal["opportunity_id"]),
        "revision":proposal["revision"],
        "proposal_status":status,
        "decision_id":str(decision_id),
        "execution_enabled":False,
    }

def refresh_build_proposals(limit: int = 100) -> int:
    with engine.connect() as db:
        ids=[row[0] for row in db.execute(text("""
          SELECT id
          FROM digital_asset_opportunities
          WHERE build_readiness='BUILD_READY'
          ORDER BY updated_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(limit,500))}).all()]
    changed=0
    for opportunity_id in ids:
        result=ensure_build_proposal(opportunity_id)
        if result and result["created"]:
            changed+=1

    with engine.begin() as db:
        db.execute(text("""
          UPDATE build_proposals bp
          SET proposal_status='STALE',updated_at=now()
          FROM digital_asset_opportunities o
          WHERE bp.opportunity_id=o.id
            AND o.build_readiness<>'BUILD_READY'
            AND bp.proposal_status<>'STALE'
        """))
        db.execute(text("""
          UPDATE digital_asset_opportunities
          SET build_proposal_status='STALE'
          WHERE build_readiness<>'BUILD_READY'
            AND build_proposal_status NOT IN ('NONE','STALE')
        """))
    return changed
