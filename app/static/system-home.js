"use strict";
const byId=id=>document.getElementById(id);
const esc=value=>String(value??"");
function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=esc(text);return n}
async function api(url){const r=await fetch(url,{cache:"no-store",headers:{Accept:"application/json"}});const b=await r.json().catch(()=>({}));if(!r.ok)throw new Error(b.detail||("HTTP "+r.status));return b}
function metric(label,value){const n=node("div","metric");n.append(node("span","",label),node("b","",value));return n}
function statusClass(value){const v=String(value||"").toUpperCase();if(["READY","CURRENT","SUCCESS","PASSED","AVAILABLE"].some(x=>v.includes(x)))return"good";if(["FAILED","ERROR","BLOCKED","UNAVAILABLE"].some(x=>v.includes(x)))return"bad";return"warn"}
function renderSystem(summary){
 const sys=summary.system||{},pill=byId("systemPill");
 pill.textContent=summary.status||"UNKNOWN";pill.className="pill "+statusClass(summary.status);
 const root=byId("systemMetrics");root.replaceChildren(
  metric("Database",sys.database_available?"AVAILABLE":"UNAVAILABLE"),
  metric("Migrations",(sys.migration_status||"UNKNOWN")+" · "+String(sys.migration_latest||"—").replace(".sql","").replace(/^\d+_/,"")),
  metric("Environment",(sys.vercel_env||"LOCAL").toUpperCase()),
  metric("Publisher",sys.publisher_adapter||"MOCK")
 );
 byId("systemNote").textContent=sys.preview_isolated?"Preview database isolated · 高风险写操作仍由独立 Gate 控制":"系统状态已载入 · 高风险写操作仍由独立 Gate 控制";
}
function renderPipeline(summary){
 const counts=summary.pipeline?.stage_counts||{};byId("jobTotal").textContent=(summary.pipeline?.total_jobs||0)+" JOBS";
 const root=byId("pipelineStages");root.replaceChildren();
 ["CONTENT_BRIEF","STORY","SCRIPT","SCENE","ASSETS","VOICES","ANIMATION","RENDER","QC","PACKAGE"].forEach(name=>{
   const n=node("div","stage"+(counts[name]?" active":""));n.append(node("span","",name.replaceAll("_"," ")),node("b","",counts[name]||0));root.appendChild(n);
 });
}
function renderSide(providers,queue){
 const root=byId("sideBusinessMetrics");root.replaceChildren();
 [["Registry Providers",providers.length],["BUILD_READY Queue",queue.length],["Automation","DAILY"],["Mode","OBSERVE"]].forEach(pair=>{
  const n=node("div","side-metric");n.append(node("span","",pair[0]),node("b","",pair[1]));root.appendChild(n);
 });
}
function isVisibleOpportunity(item){
 const title=String(item.title||item.canonical_title||"");
 const source=String(item.source_url||"");
 return !title.startsWith("[TEST_ONLY]")
  && !title.startsWith("[PREVIEW_ONLY]")
  && !source.startsWith("vercel-preview://")
  && !source.includes("example.invalid/");
}
function renderOpportunities(items){
 const root=byId("opportunityGrid");root.replaceChildren();
 const visible=items.filter(isVisibleOpportunity);if(!visible.length){root.append(node("div","opportunity-card","当前暂无已验证的正式机会数据"));return}
 visible.slice(0,6).forEach(item=>{
  const card=node("article","opportunity-card"),head=node("header"),left=node("div"),score=node("div","score",Math.round(Number(item.score||0)));
  left.append(node("div","kicker",item.asset_type||"OPPORTUNITY"),node("h3","",item.canonical_title||item.title||"Untitled"));
  head.append(left,score);card.append(head,node("p","",item.monetization_model||"等待研究验证"));
  const meta=node("div","meta-row");
  [item.build_readiness||item.status,"Evidence "+(item.evidence_count??0),"Sources "+(item.independent_source_count??0)].forEach(x=>meta.append(node("span","chip",x||"UNKNOWN")));
  card.append(meta);root.append(card);
 });
}
async function load(){
 try{
  const [summary,providers,queue,opps]=await Promise.all([
   api("/v1/shrimp-animation/control-center/summary"),
   api("/v1/side-business/providers?limit=100"),
   api("/v1/side-business/build-queue?limit=100"),
   api("/v1/opportunities?limit=30")
  ]);
  renderSystem(summary);renderPipeline(summary);renderSide(providers,queue);renderOpportunities(opps);
 }catch(err){
  const pill=byId("systemPill");pill.textContent="DEGRADED";pill.className="pill bad";
  byId("systemNote").textContent="首页数据载入失败："+err.message;
 }
}
load();