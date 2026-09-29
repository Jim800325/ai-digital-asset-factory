"use strict";

const state={index:null,records:[],selected:null};
const byId=(id)=>document.getElementById(id);

function node(tag,cls,text){
  const el=document.createElement(tag);
  if(cls) el.className=cls;
  if(text!==undefined&&text!==null) el.textContent=String(text);
  return el;
}
function clear(el){el.replaceChildren();}
function shortHash(value){if(!value)return "—";return value.length>18?value.slice(0,10)+"…"+value.slice(-7):value;}
function fmtDate(value){
  if(!value)return "—";
  try{return new Intl.DateTimeFormat("zh-Hant",{year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date(value));}
  catch{return String(value);}
}
function fmtMoney(value){return "$"+Number(value||0).toFixed(6);}
function statusClass(value){
  const s=String(value||"").toUpperCase();
  if(["PASS","PASSED","WITHIN_BUDGET","VERIFIED","YES"].includes(s)) return "good";
  if(["FAILED","BLOCKED","NO"].includes(s)) return "bad";
  return "info";
}
function addPill(parent,text,value){
  const p=node("span","pill "+statusClass(value),text);
  parent.appendChild(p);return p;
}
function summaryCard(label,value,note){
  const card=node("div","summary-card");
  card.appendChild(node("div","summary-label",label));
  card.appendChild(node("div","summary-value",value));
  if(note) card.appendChild(node("div","metric-note",note));
  return card;
}
function kv(label,value,mono=false){
  const wrap=node("div","kv");
  wrap.appendChild(node("div","kv-label",label));
  wrap.appendChild(node("div","kv-value"+(mono?" code":""),value===null||value===undefined?"—":value));
  return wrap;
}
function section(title){
  const card=node("div","section-card");
  if(title) card.appendChild(node("h3","",title));
  return card;
}
function showToast(message,error=false){
  const toast=byId("toast");
  toast.textContent=message;
  toast.className="toast"+(error?" bad":"");
  toast.classList.remove("hidden");
  clearTimeout(showToast._timer);
  showToast._timer=setTimeout(()=>toast.classList.add("hidden"),3500);
}
async function api(url){
  const r=await fetch(url,{cache:"no-store",headers:{"Accept":"application/json"}});
  const p=await r.json();
  if(!r.ok) throw new Error(p.detail||JSON.stringify(p));
  return p;
}
function renderIndex(){
  const s=byId("registrySummary");clear(s);
  const i=state.index||{};
  s.append(
    summaryCard("Records",i.record_count||0,"repository JSON"),
    summaryCard("Passed",i.passed_count||0,"final acceptance"),
    summaryCard("Gateway Requests",i.total_gateway_requests||0,"historical only"),
    summaryCard("Total Tokens",Number(i.total_tokens||0).toLocaleString(),"historical usage"),
    summaryCard("Estimated Cost",fmtMoney(i.total_estimated_cost_usd),"historical aggregate")
  );
}
function renderList(){
  const list=byId("auditList");clear(list);
  byId("auditCount").textContent=state.records.length+" 笔记录";
  for(const item of state.records){
    const btn=node("button","candidate");
    btn.type="button";
    if(state.selected?.audit_id===item.audit_id) btn.classList.add("active");
    const head=node("div","audit-list-title");
    head.appendChild(node("strong","",item.audit_id||"unknown"));
    addPill(head,item.acceptance_status,item.acceptance_status);
    btn.appendChild(head);
    btn.appendChild(node("div","candidate-meta",(item.provider||"—")+" · "+(item.model||"—")+" · "+Number(item.total_tokens||0).toLocaleString()+" tokens"));
    btn.appendChild(node("div","candidate-hash","deploy "+shortHash(item.vercel_deployment_id)+" · "+fmtDate(item.occurred_at_utc)));
    btn.appendChild(node("div","audit-cost",fmtMoney(item.estimated_cost_usd)));
    btn.addEventListener("click",()=>selectAudit(item.audit_id,true));
    list.appendChild(btn);
  }
}
async function load(){
  try{
    state.index=await api("/v1/live-acceptance-audits/evidence-index");
    state.records=state.index.records||[];
    renderIndex();renderList();
    const deep=location.pathname.startsWith("/review/audits/")?location.pathname.slice("/review/audits/".length):"";
    const target=deep||state.selected?.audit_id||state.records[0]?.audit_id;
    if(target) await selectAudit(target,false);
  }catch(err){showToast("载入失败："+err.message,true);}
}
async function selectAudit(id,push){
  try{
    const d=await api("/v1/live-acceptance-audits/"+encodeURIComponent(id));
    state.selected=d;
    if(push) history.pushState({auditId:id},"","/review/audits/"+id);
    renderList();renderDetail(d);
  }catch(err){showToast("Audit 载入失败："+err.message,true);}
}
function renderDetail(d){
  byId("emptyState").classList.add("hidden");
  byId("detailContent").classList.remove("hidden");
  byId("auditKicker").textContent="Audit · "+d.audit_id;
  byId("auditTitle").textContent=(d.provider||"—")+" / "+(d.model||"—");
  byId("auditSubtitle").textContent=fmtDate(d.occurred_at_utc)+" · "+(d._evidence?.filename||"repository evidence");

  const badges=byId("auditBadges");clear(badges);
  addPill(badges,d.acceptance_status,d.acceptance_status);
  addPill(badges,d.budget_status,d.budget_status);
  addPill(badges,d.live_model_verified?"LIVE VERIFIED":"NOT VERIFIED",d.live_model_verified?"VERIFIED":"NO");
  addPill(badges,"READ ONLY","YES");

  const sum=byId("auditSummary");clear(sum);
  sum.append(
    summaryCard("Requests",d.gateway_request_count||0),
    summaryCard("Tokens",Number(d.total_tokens||0).toLocaleString()),
    summaryCard("Cost",fmtMoney(d.estimated_cost_usd)),
    summaryCard("Artifacts",d.artifact_count||0),
    summaryCard("Duplicates",d.duplicate_requests_observed||0)
  );

  renderOverview(d);renderArtifacts(d);renderProvenance(d);renderRaw(d);
}
function renderOverview(d){
  const panel=byId("tab-overview");clear(panel);
  const c=section("Acceptance Evidence");
  const g=node("div","kv-grid");
  g.append(
    kv("Status",d.acceptance_status),
    kv("Phase",d.phase),
    kv("Provider",d.provider),
    kv("Model",d.model),
    kv("Gateway mode",d.gateway_mode),
    kv("Live model verified",d.live_model_verified?"YES":"NO"),
    kv("Budget status",d.budget_status),
    kv("Tests passed",d.tests_passed?"YES":"NO"),
    kv("Gateway requests",d.gateway_request_count),
    kv("Prompt tokens",d.prompt_tokens),
    kv("Completion tokens",d.completion_tokens),
    kv("Total tokens",d.total_tokens),
    kv("Estimated cost",fmtMoney(d.estimated_cost_usd)),
    kv("Hard max cost",fmtMoney(d.max_cost_usd)),
    kv("Duplicate requests observed",d.duplicate_requests_observed||0),
    kv("Duplicate suppression",d.duplicate_suppression_verified?"VERIFIED":"NO")
  );
  c.appendChild(g);panel.appendChild(c);
  const safety=section("Safety Boundary");
  const sg=node("div","kv-grid");
  sg.append(
    kv("External side effects",d.external_side_effects),
    kv("Deployment enabled",d.deployment_enabled?"YES":"NO"),
    kv("Git push enabled",d.git_push_enabled?"YES":"NO"),
    kv("Release approved",d.release_approved?"YES":"NO"),
    kv("Agent network",d.agent_network_policy),
    kv("Gateway network",d.gateway_network_policy)
  );
  safety.appendChild(sg);panel.appendChild(safety);
}
function renderArtifacts(d){
  const panel=byId("tab-artifacts");clear(panel);
  const card=section("Artifact Hashes");
  const wrap=node("div","table-wrap");
  const table=node("table");
  const head=node("thead");const hr=node("tr");
  ["Path","Bytes","SHA-256"].forEach(x=>hr.appendChild(node("th","",x)));
  head.appendChild(hr);table.appendChild(head);
  const body=node("tbody");
  (d.artifacts||[]).forEach(a=>{
    const tr=node("tr");
    tr.append(node("td","code",a.relative_path),node("td","",a.byte_size),node("td","hash",a.sha256));
    body.appendChild(tr);
  });
  table.appendChild(body);wrap.appendChild(table);card.appendChild(wrap);panel.appendChild(card);
}
function renderProvenance(d){
  const panel=byId("tab-provenance");clear(panel);
  const card=section("Repository / Deployment Provenance");
  const g=node("div","kv-grid");
  g.append(
    kv("Audit ID",d.audit_id,true),
    kv("Source commit",d.source_commit,true),
    kv("Vercel deployment ID",d.vercel_deployment_id,true),
    kv("Source tree SHA-256",d.source_tree_sha256,true),
    kv("Evidence file",d._evidence?.filename,true),
    kv("Evidence SHA-256",d._evidence?.sha256,true),
    kv("Evidence bytes",d._evidence?.byte_size),
    kv("Registry backend",d._evidence?.registry_backend),
    kv("Read only",d._evidence?.read_only?"YES":"NO")
  );
  card.appendChild(g);panel.appendChild(card);
}
function renderRaw(d){
  const panel=byId("tab-raw");clear(panel);
  const card=section("Normalized JSON Evidence");
  const pre=node("pre","diff-pre raw-json",JSON.stringify(d,null,2));
  card.appendChild(pre);panel.appendChild(card);
}
function activateTab(name){
  document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("active",x.dataset.tab===name));
  document.querySelectorAll(".tab-panel").forEach(x=>x.classList.toggle("active",x.id==="tab-"+name));
}
document.querySelectorAll(".tab").forEach(x=>x.addEventListener("click",()=>activateTab(x.dataset.tab)));
byId("refreshButton").addEventListener("click",async()=>{await load();showToast("已重新整理；未触发任何模型调用。");});
window.addEventListener("popstate",()=>load());
load();
