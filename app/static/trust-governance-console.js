"use strict";

const byId=(id)=>document.getElementById(id);

function clear(node){node.replaceChildren();}
function el(tag,cls,text){
  const node=document.createElement(tag);
  if(cls)node.className=cls;
  if(text!==undefined)node.textContent=String(text);
  return node;
}
function toast(message,bad=false){
  const node=byId("toast");
  node.textContent=message;
  node.className="toast"+(bad?" bad":"");
  node.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer=setTimeout(()=>node.classList.add("hidden"),3200);
}
async function api(url){
  const response=await fetch(url,{cache:"no-store",headers:{"Accept":"application/json"}});
  const body=await response.json().catch(()=>({detail:"Invalid response"}));
  if(!response.ok)throw new Error(body.detail||("HTTP "+response.status));
  return body;
}
function pillClass(value){
  const v=String(value||"").toUpperCase();
  if(["PASS","PASSED","VERIFIED","CURRENT","CERTIFIED","NORMAL","CROSS_CLOUD_ACCEPTED","CLEANUP_VERIFIED","ACTIVE"].some(x=>v.includes(x)))return "good";
  if(["FAIL","FAILED","BROKEN","REVOKED","EXPIRED","CRITICAL","INVALID"].some(x=>v.includes(x)))return "bad";
  return "warn";
}
function setStatus(id,value){
  const node=byId(id);
  node.textContent=value||"UNKNOWN";
  node.className="pill "+pillClass(value);
}
function short(value){
  const s=String(value||"");
  return s.length>18?s.slice(0,10)+"…"+s.slice(-6):s||"—";
}
function metric(label,value,small=false){
  const card=el("div","metric-card");
  card.append(el("div","metric-label",label),el("div","metric-value"+(small?" small":""),value??"—"));
  return card;
}
function evidence(title,status,detail){
  const card=el("div","evidence-item");
  const head=el("div","evidence-head");
  head.append(el("strong","",title));
  const pill=el("span","pill "+pillClass(status),status||"—");
  head.append(pill);
  card.append(head,el("div","muted mono",detail||"—"));
  return card;
}
function safeData(section){
  return section&&section.available?section.data||{}:{};
}
function renderSummary(data){
  const status=data.status||{}, summary=data.summary||{};
  setStatus("trustStatus",status.trust);
  setStatus("cloudStatus",status.cloud_kms);
  setStatus("certStatus",status.certification);
  setStatus("govStatus",status.governance);

  const available=Object.values(data.sections||{}).filter(x=>x.available).length;
  const total=Object.keys(data.sections||{}).length;
  setStatus("consoleStatus",available===total?"READY":"DEGRADED");

  const trust=byId("trustMetrics");clear(trust);
  trust.append(
    metric("Active keys",summary.active_signing_keys||0),
    metric("Trust roots",summary.trust_root_count||0),
    metric("Root transitions",summary.root_transition_count||0),
    metric("HSM keys",summary.hsm_key_count||0)
  );

  const cloud=byId("cloudMetrics");clear(cloud);
  cloud.append(
    metric("Registry",summary.external_kms_provider_count||0),
    metric("Accepted clouds",(summary.accepted_cloud_provider_types||[]).length),
    metric("Live runs",summary.live_cloud_acceptance_count||0),
    metric("Cross-cloud",summary.cross_cloud_ceremony_count||0)
  );

  const cert=byId("certMetrics");clear(cert);
  cert.append(
    metric("Current",summary.certification_current?"YES":"NO"),
    metric("Expires days",summary.certification_expires_in_days??"—"),
    metric("Trust audit",summary.trust_chain_valid?"PASS":"ATTENTION"),
    metric("Recert",summary.recertification_required?"REQUIRED":"NO")
  );

  const gov=byId("govMetrics");clear(gov);
  gov.append(
    metric("Reviews",summary.governance_review_count||0),
    metric("Policy intents",summary.policy_intent_count||0),
    metric("Apply","DISABLED",true),
    metric("Prod writes","DISABLED",true)
  );
}
function renderSigning(data){
  const signing=safeData(data.sections.signing);
  const root=byId("keyLifecycle");clear(root);
  const provider=signing.signing_provider||{};
  root.append(
    evidence("Signing provider",provider.status||provider.provider||"CONFIGURED",provider.provider||provider.provider_type||"provider"),
    evidence("Trust root chain",signing.trust_root_chain?.verification_status||"UNKNOWN","roots "+(signing.trust_roots||[]).length),
    evidence("Bundle verification",signing.bundle_verification?.verification_status||signing.bundle_verification?.status||"AVAILABLE","active keys "+(signing.active_key_count||0))
  );
  (signing.signing_keys||[]).slice(0,4).forEach(item=>{
    root.append(evidence(
      "Key "+short(item.fingerprint_sha256||item.key_fingerprint_sha256),
      item.lifecycle_status||"UNKNOWN",
      [item.provider||item.provider_type,item.algorithm].filter(Boolean).join(" · ")
    ));
  });

  const multi=safeData(data.sections.multisigner);
  const transitions=byId("rootTransitions");clear(transitions);
  transitions.append(
    evidence("Threshold model",multi.trust_model||"python-tuf-root-continuity","engine "+(multi.threshold_engine||"—")),
    evidence("Dual control",multi.dual_control_required?"REQUIRED":"UNKNOWN","minimum approvals "+(multi.minimum_human_approvals??"—"))
  );
  (multi.plans||[]).slice(0,5).forEach(plan=>{
    transitions.append(evidence(
      "Root transition "+short(plan.id),
      plan.plan_status||plan.transition_status||"UNKNOWN",
      "from "+(plan.from_root_version??"—")+" → "+(plan.to_root_version??"—")
    ));
  });
}
function renderCustody(data){
  const hsm=safeData(data.sections.hsm);
  const h=byId("hsmPanel");clear(h);
  const ready=hsm.hsm||{};
  h.append(
    evidence("PKCS#11",ready.status||ready.provider||"CONFIGURED","keys "+(hsm.keys||[]).length),
    evidence("Root ceremonies",(hsm.ceremonies||[]).length?"RECORDED":"NONE","count "+(hsm.ceremonies||[]).length),
    evidence("Private key export",hsm.private_key_export_allowed?"ENABLED":"DISABLED","must remain disabled")
  );
  (hsm.restore_drills||[]).slice(0,3).forEach(x=>{
    h.append(evidence("Restore drill "+short(x.id),x.drill_status||"RECORDED",x.executed_at||""));
  });

  const ext=safeData(data.sections.external_kms);
  const e=byId("externalKmsPanel");clear(e);
  e.append(
    evidence("External KMS",ext.external_kms_enabled?"ENABLED":"DISABLED","providers "+(ext.providers||[]).length),
    evidence("Failover",ext.failover_enabled?"ENABLED":"DISABLED","runs "+(ext.failover_runs||[]).length),
    evidence("Private key export",ext.private_key_export_allowed?"ENABLED":"DISABLED","credentials persisted: "+String(ext.credentials_persisted===true))
  );
  (ext.providers||[]).slice(0,6).forEach(p=>{
    e.append(evidence(p.provider_type||p.provider||"KMS",p.provider_status||p.status||"REGISTERED",p.provider_ref||p.key_locator||""));
  });
}
function renderCloud(data){
  const cloud=safeData(data.sections.live_cloud_kms);
  const grid=byId("cloudProviders");clear(grid);
  const accepted=new Set(cloud.accepted_provider_types||[]);
  ["AWS_KMS","GCP_KMS","AZURE_KEY_VAULT"].forEach(name=>{
    const card=el("article","provider-card");
    card.append(
      el("div","card-kicker",name),
      el("h3","",accepted.has(name)?"LIVE ACCEPTED":"NOT LIVE ACCEPTED")
    );
    const p=el("span","pill "+pillClass(accepted.has(name)?"PASSED":"PENDING"),accepted.has(name)?"PASSED":"PENDING");
    card.appendChild(p);grid.appendChild(card);
  });
  const runs=byId("cloudRuns");clear(runs);
  (cloud.acceptances||[]).slice(0,8).forEach(run=>{
    runs.append(evidence(
      (run.provider_type||"CLOUD")+" · "+short(run.resource_name||run.id),
      run.acceptance_status||"UNKNOWN",
      "sign="+String(run.live_signature_verified===true)+" · cleanup="+String(run.cleanup_verified===true)+" · blocked="+String(run.post_cleanup_sign_blocked===true)
    ));
  });
  if(!(cloud.acceptances||[]).length){
    runs.append(evidence("Real cloud acceptance","NOT EXECUTED","10B.27A remains pending; this console does not trigger it."));
  }
}
function renderTransparency(data){
  const trans=safeData(data.sections.transparency);
  const counts=trans.counts||{};
  const root=byId("transparencyMetrics");clear(root);
  root.append(
    metric("DSSE",counts.attestations||0),
    metric("Timestamps",counts.timestamps||0),
    metric("Transparency",counts.transparency_entries||0),
    metric("Offline bundles",counts.offline_bundles||0)
  );
  const external=safeData(data.sections.external_verification);
  const panel=byId("verificationPanel");clear(panel);
  panel.append(
    evidence("Offline verification",trans.offline_verification?"ENABLED":"DISABLED","private key required: "+String(trans.private_key_required_for_verification===true)),
    evidence("Export registry",external.export_registry_verification?.verification_status||external.export_registry_verification?.status||"UNKNOWN","proof bundles "+(external.proof_bundles||[]).length),
    evidence("External network writes",external.external_network_writes_enabled?"ENABLED":"DISABLED","automatic policy change: "+String(external.automatic_policy_change===true))
  );
}
function renderCertification(data){
  const cert=safeData(data.sections.certification);
  const renewal=safeData(data.sections.renewal);
  const audit=safeData(data.sections.trust_audit);
  const current=renewal.current_certification||cert.current_certification||{};

  const c=byId("certificationPanel");clear(c);
  c.append(
    evidence("Certification",current.certification_status||"NOT_CERTIFIED","id "+short(current.id)),
    evidence("Expiry",renewal.recertification_required?"RECERTIFICATION REQUIRED":"CURRENT","expires in "+(renewal.expires_in_days??"—")+" days"),
    evidence("Long-term SLO",cert.long_term_slo_promoted?"PROMOTED":"NOT PROMOTED","baseline immutable: "+String(cert.baseline_immutable===true))
  );

  const a=byId("auditPanel");clear(a);
  a.append(
    evidence("Certification trust chain",audit.trust_chain_valid?"PASS":"ATTENTION","integrity audits "+(audit.integrity_audits||[]).length),
    evidence("Renewal SLA",audit.renewal_sla_hours?"CONFIGURED":"UNKNOWN",(audit.renewal_sla_hours??"—")+"h"),
    evidence("Missed renewal critical",audit.missed_renewal_critical_hours?"CONFIGURED":"UNKNOWN",(audit.missed_renewal_critical_hours??"—")+"h")
  );
}
function renderGovernance(data){
  const gov=safeData(data.sections.governance);
  const panel=byId("governancePanel");clear(panel);
  const current=gov.current_review;
  panel.append(
    evidence("Human gate",gov.human_gate_required?"REQUIRED":"UNKNOWN","execution supported: "+String(gov.execution_supported===true)),
    evidence("Policy application",gov.changes_applied?"APPLIED":"DISABLED","console remains observation-only")
  );
  if(current){
    panel.append(evidence(
      "Current review "+short(current.id),
      current.review_status||"PENDING_DECISION",
      current.recommendation||current.reason||""
    ));
  }
  (gov.policy_intents||[]).slice(0,6).forEach(intent=>{
    panel.append(evidence(
      "Policy intent "+short(intent.id),
      intent.intent_status||intent.status||"RECORDED",
      intent.intent_type||intent.policy_action||""
    ));
  });
}
function renderReferences(data){
  const root=byId("referencePanel");clear(root);
  Object.entries(data.references||{}).forEach(([key,value])=>{
    const card=el("div","reference-card");
    card.append(el("div","card-kicker",key.replaceAll("_"," ")),el("strong","",value));
    root.appendChild(card);
  });
}
function render(data){
  renderSummary(data);
  renderSigning(data);
  renderCustody(data);
  renderCloud(data);
  renderTransparency(data);
  renderCertification(data);
  renderGovernance(data);
  renderReferences(data);
}
async function load(){
  byId("refreshButton").disabled=true;
  try{
    render(await api("/v1/shrimp-animation/trust-governance-console"));
  }catch(err){
    setStatus("consoleStatus","ERROR");
    toast("Trust/Governance 载入失败："+err.message,true);
  }finally{
    byId("refreshButton").disabled=false;
  }
}
byId("refreshButton").addEventListener("click",async()=>{await load();toast("已重新整理");});
load();
