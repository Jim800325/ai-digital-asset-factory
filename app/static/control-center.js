"use strict";

const byId=(id)=>document.getElementById(id);
const state={summary:null};

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
  if(["READY","SUCCESS","SUCCEEDED","PASSED","QC_PASSED","RELEASE_APPROVED","PUBLISH_AUTHORIZED","PUBLISHED","CLEANED_UP","CURRENT","ENABLED"].some(x=>v.includes(x)))return "good";
  if(["FAILED","REJECTED","BLOCKED","STALE","MISCONFIGURED","UNKNOWN"].some(x=>v.includes(x)))return "bad";
  return "warn";
}
function formatDate(value){
  if(!value)return "—";
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return String(value);
  return date.toLocaleString(undefined,{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});
}
function shortId(value){
  const s=String(value||"");
  return s.length>14?s.slice(0,8)+"…"+s.slice(-4):s||"—";
}
function metric(label,value,small=false){
  const card=el("div","metric-card");
  card.append(el("div","metric-label",label),el("div","metric-value"+(small?" small":""),value??"—"));
  return card;
}
function addPill(target,text){
  const p=el("span","pill "+pillClass(text),text);
  target.appendChild(p);
}
function renderSystem(summary){
  const system=summary.system||{};
  byId("systemStatus").textContent=summary.status||"UNKNOWN";
  byId("systemStatus").className="pill "+pillClass(summary.status);
  byId("environmentBadge").textContent=(system.vercel_env||"ENV").toUpperCase()+" · "+(system.preview_isolated?"ISOLATED DB":"DB");
  byId("systemSubtitle").textContent=[
    system.database_source||"DB",
    system.migration_status||"MIGRATIONS",
    system.publisher_adapter||"ADAPTER"
  ].join(" · ");
  const metrics=byId("systemMetrics");clear(metrics);
  metrics.append(
    metric("Database",system.database_available?"AVAILABLE":"UNAVAILABLE"),
    metric("Migrations",(system.migration_applied_count??"—")+" / "+(system.migration_expected_count??"—")),
    metric("Latest",system.migration_latest||"—",true),
    metric("Adapter",system.publisher_adapter||"—",true)
  );
}
function renderPipeline(summary){
  const pipeline=summary.pipeline||{};
  byId("jobTotal").textContent=(pipeline.total_jobs||0)+" JOBS";
  const stages=["CONTENT_BRIEF","STORY","SCRIPT","SCENE","ASSETS","VOICES","ANIMATION","RENDER","QC","PACKAGE"];
  const counts=pipeline.stage_counts||{};
  const root=byId("pipelineStages");clear(root);
  stages.forEach(name=>{
    const count=counts[name]||0;
    const card=el("div","stage-card"+(count?" stage-active":""));
    card.append(el("div","stage-name",name.replace("_"," ")),el("div","stage-count",count));
    root.appendChild(card);
  });
}
function renderWorkspaces(summary){
  const review=summary.review||{};
  const publishing=summary.publishing||{};
  byId("reviewWorkspaceStat").textContent=(review.release_approved||0)+" APPROVED";
  byId("publishWorkspaceStat").textContent=(publishing.plan_status_counts?.PUBLISH_AUTHORIZED||0)+" AUTHORIZED";
}
const checkLabels={
  vercel_preview:"Vercel Preview",
  preview_database_isolated:"Preview DB isolated",
  publish_authorization_gate:"Step 9 authorization gate",
  publish_execution_gate:"Step 10 execution gate",
  publish_executor_enabled:"Publisher executor enabled",
  bilibili_controlled_adapter:"BILIBILI_CONTROLLED",
  bilibili_live_acceptance_gate:"Step 10B live gate",
  bilibili_live_acceptance_enabled:"Step 10B enabled",
  bilibili_cookie_credentials_present:"Bilibili Cookie credentials",
  sacrificial_account_allowlist_present:"Sacrificial MID allowlist",
  real_account_denylist_present:"Main MID denylist",
  sacrificial_target_allowlist_present:"Sacrificial target allowlist",
  real_target_denylist_present:"Main target denylist",
  allowlist_denylist_disjoint:"Allow / deny disjoint",
  runnable_bilibili_execution_present:"Runnable Bilibili execution"
};
function renderBilibili(summary){
  const bili=summary.bilibili||{};
  const readiness=bili.readiness||{};
  const status=readiness.status||"UNKNOWN";
  byId("bilibiliStatus").textContent=status;
  byId("bilibiliStatus").className="pill "+pillClass(status);

  const quick=byId("bilibiliQuick");clear(quick);
  const counts=readiness.counts||{};
  quick.append(
    metric("Active targets",counts.active_bilibili_targets??0),
    metric("Authorized plans",counts.authorized_bilibili_plans??0),
    metric("Controlled exec",counts.bilibili_controlled_executions??0),
    metric("Live acceptance",bili.acceptance_count??0)
  );

  const blockers=readiness.blockers||[];
  const notice=byId("blockerSummary");
  if(status==="READY"){
    notice.className="notice";
    notice.textContent="Bilibili Step 10B 已 READY。真实执行仍必须使用独立 Live Acceptance Key。";
  }else{
    notice.className="notice warn";
    notice.textContent="当前 BLOCKED："+(blockers.length?blockers.map(k=>checkLabels[k]||k).join("、"):"未知 blocker");
  }

  const grid=byId("readinessChecks");clear(grid);
  Object.entries(readiness.checks||{}).forEach(([key,value])=>{
    const item=el("div","check-item");
    item.append(
      el("span","",checkLabels[key]||key.replaceAll("_"," ")),
      el("span","check-state "+(value?"good":"bad"),value?"PASS":"BLOCKED")
    );
    grid.appendChild(item);
  });
}
function renderPublishing(summary){
  const p=summary.publishing||{};
  const root=byId("publishingMetrics");clear(root);
  root.append(
    metric("Targets",p.target_count||0),
    metric("Plans",p.plan_count||0),
    metric("Authorized",p.plan_status_counts?.PUBLISH_AUTHORIZED||0),
    metric("Executions",p.execution_count||0)
  );
}
function emptyRow(tbody,colspan,text){
  const tr=document.createElement("tr");
  const td=el("td","empty-row",text);
  td.colSpan=colspan;tr.appendChild(td);tbody.appendChild(tr);
}
function renderJobs(summary){
  const body=byId("recentJobsBody");clear(body);
  const rows=summary.pipeline?.recent_jobs||[];
  if(!rows.length){emptyRow(body,4,"尚无 Shrimp pipeline job");return;}
  rows.forEach(job=>{
    const tr=document.createElement("tr");
    tr.append(
      el("td","mono",shortId(job.id)),
      el("td","",job.current_stage||"—")
    );
    const status=document.createElement("td");addPill(status,job.job_status||"UNKNOWN");tr.appendChild(status);
    tr.appendChild(el("td","",formatDate(job.updated_at)));
    body.appendChild(tr);
  });
}
function renderEpisodes(summary){
  const body=byId("recentEpisodesBody");clear(body);
  const rows=summary.review?.recent_episodes||[];
  if(!rows.length){emptyRow(body,4,"尚无进入 Review 的 Episode");return;}
  rows.forEach(item=>{
    const tr=document.createElement("tr");
    tr.appendChild(el("td","",item.title||item.episode_id||"Episode"));
    const review=document.createElement("td");addPill(review,item.review_status||"UNKNOWN");tr.appendChild(review);
    const qc=document.createElement("td");addPill(qc,item.qc_passed===true?"QC PASSED":item.job_status||"UNKNOWN");tr.appendChild(qc);
    const open=document.createElement("td");
    const a=el("a","table-link","打开");
    a.href="/animation-review/"+item.job_id;open.appendChild(a);tr.appendChild(open);
    body.appendChild(tr);
  });
}
function renderExecutions(summary){
  const body=byId("recentExecutionsBody");clear(body);
  const rows=summary.publishing?.recent_executions||[];
  if(!rows.length){emptyRow(body,6,"尚无 Controlled Publisher Execution");return;}
  rows.forEach(item=>{
    const tr=document.createElement("tr");
    tr.append(el("td","",item.platform||"—"));
    const target=document.createElement("td");
    const a=el("a","table-link",item.target_key||"—");
    a.href="/animation-publishing/"+item.provider_job_id;target.appendChild(a);tr.appendChild(target);
    const status=document.createElement("td");addPill(status,item.execution_status||"UNKNOWN");tr.appendChild(status);
    tr.append(
      el("td","",(item.upload_outcome||"—")+" · "+(item.upload_write_count??0)+"/1"),
      el("td","",(item.publish_outcome||"—")+" · "+(item.publish_write_count??0)+"/1")
    );
    const source=document.createElement("td");addPill(source,item.source_stale?"STALE":"CURRENT");tr.appendChild(source);
    body.appendChild(tr);
  });
}
function render(summary){
  state.summary=summary;
  renderSystem(summary);
  renderPipeline(summary);
  renderWorkspaces(summary);
  renderBilibili(summary);
  renderPublishing(summary);
  renderJobs(summary);
  renderEpisodes(summary);
  renderExecutions(summary);
}
async function load(){
  byId("refreshButton").disabled=true;
  try{
    const summary=await api("/v1/shrimp-animation/control-center/summary");
    render(summary);
  }catch(err){
    toast("Control Center 载入失败："+err.message,true);
    byId("systemStatus").textContent="ERROR";
    byId("systemStatus").className="pill bad";
  }finally{
    byId("refreshButton").disabled=false;
  }
}
byId("refreshButton").addEventListener("click",async()=>{await load();toast("已重新整理");});
byId("copyBlockersButton").addEventListener("click",async()=>{
  const blockers=state.summary?.bilibili?.readiness?.blockers||[];
  const text=blockers.map(k=>checkLabels[k]||k).join("\n")||"READY";
  try{await navigator.clipboard.writeText(text);toast("Blocker 已复制");}
  catch(_){toast("浏览器不允许复制",true);}
});
load();
