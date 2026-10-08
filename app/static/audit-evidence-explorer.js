"use strict";

const byId=(id)=>document.getElementById(id);
const state={events:[],selected:null,offset:0,limit:200,hasMore:false,categories:[],statuses:[]};

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
  toast.timer=setTimeout(()=>node.classList.add("hidden"),3000);
}
async function api(url){
  const response=await fetch(url,{cache:"no-store",headers:{"Accept":"application/json"}});
  const body=await response.json().catch(()=>({detail:"Invalid response"}));
  if(!response.ok)throw new Error(body.detail||("HTTP "+response.status));
  return body;
}
function pillClass(value){
  const v=String(value||"").toUpperCase();
  if(["PASS","PASSED","SUCCEEDED","CERTIFIED","STABLE","HEALTHY","ACTIVE","CLOSED","APPLIED","ACCEPTED","ATTESTED","INCLUDED","GENERATED","PUBLISHED","CLEANUP_VERIFIED"].some(x=>v.includes(x)))return "good";
  if(["FAIL","FAILED","CRITICAL","OPEN","UNKNOWN","STALE","REJECTED","UNHEALTHY"].some(x=>v.includes(x)))return "bad";
  return "warn";
}
function addPill(target,text){
  target.appendChild(el("span","pill "+pillClass(text),text||"—"));
}
function short(value,n=18){
  const s=String(value||"");
  return s.length>n?s.slice(0,10)+"…"+s.slice(-6):s||"—";
}
function formatDate(value){
  if(!value)return "—";
  const d=new Date(value);
  if(Number.isNaN(d.getTime()))return String(value);
  return d.toLocaleString();
}
function inputIso(id){
  const value=byId(id).value;
  if(!value)return "";
  const d=new Date(value);
  return Number.isNaN(d.getTime())?"":d.toISOString();
}
function buildParams(includePage=true){
  const p=new URLSearchParams();
  const category=byId("categoryFilter").value;
  const status=byId("statusFilter").value;
  const q=byId("queryFilter").value.trim();
  const after=inputIso("afterFilter");
  const before=inputIso("beforeFilter");
  if(category)p.set("category",category);
  if(status)p.set("status",status);
  if(q)p.set("q",q);
  if(after)p.set("after",after);
  if(before)p.set("before",before);
  if(includePage){
    p.set("limit",String(state.limit));
    p.set("offset",String(state.offset));
  }
  return p;
}
function populateSelect(id,values,label){
  const select=byId(id);
  const current=select.value;
  clear(select);
  const first=document.createElement("option");
  first.value="";first.textContent=label;select.appendChild(first);
  values.forEach(value=>{
    const option=document.createElement("option");
    option.value=value;option.textContent=value;select.appendChild(option);
  });
  if(values.includes(current))select.value=current;
}
function renderSummary(data){
  const summary=data.summary||{};
  byId("totalEvents").textContent=summary.total??0;
  byId("immutableEvents").textContent=summary.immutable_events??0;
  byId("hashedEvents").textContent=summary.with_evidence_sha256??0;
  byId("categoryCount").textContent=Object.keys(summary.by_category||{}).length;
  byId("resultBadge").textContent=(summary.total??0)+" MATCHES";
  byId("resultBadge").className="pill info";
  state.categories=data.categories||[];
  state.statuses=Object.keys(summary.by_status||{}).sort();
  populateSelect("categoryFilter",state.categories,"ALL CATEGORIES");
  populateSelect("statusFilter",state.statuses,"ALL STATUSES");
  const coverage=byId("categoryCoverage");clear(coverage);
  state.categories.forEach(name=>{
    const card=el("div","coverage-item");
    card.append(
      el("div","coverage-name",name),
      el("div","coverage-value",(summary.by_category||{})[name]||0)
    );
    coverage.appendChild(card);
  });
}
function renderTimeline(){
  const body=byId("timelineBody");clear(body);
  if(!state.events.length){
    const tr=document.createElement("tr");
    const td=el("td","empty-row","没有匹配的 Audit / Evidence 记录");
    td.colSpan=6;tr.appendChild(td);body.appendChild(tr);
    return;
  }
  state.events.forEach(item=>{
    const tr=document.createElement("tr");
    tr.dataset.eventId=item.id;
    if(state.selected&&state.selected.id===item.id)tr.classList.add("selected");
    tr.appendChild(el("td","",formatDate(item.occurred_at)));
    const cat=document.createElement("td");
    cat.appendChild(el("span","category-chip",item.category));
    tr.appendChild(cat);
    const event=document.createElement("td");
    event.append(
      el("div","event-title",item.title),
      el("div","event-kind",item.kind)
    );
    tr.appendChild(event);
    const status=document.createElement("td");addPill(status,item.status);tr.appendChild(status);
    tr.appendChild(el("td","sha",short(item.evidence_sha256,26)));
    tr.appendChild(el("td","trace-link",short(item.parent_id||item.source_id,22)));
    tr.addEventListener("click",()=>selectEvent(item));
    body.appendChild(tr);
  });
}
function renderDetail(item){
  const empty=byId("detailEmpty");
  const content=byId("detailContent");
  const copy=byId("copyShaButton");
  if(!item){
    empty.classList.remove("hidden");
    content.classList.add("hidden");
    copy.disabled=true;
    return;
  }
  empty.classList.add("hidden");
  content.classList.remove("hidden");
  copy.disabled=!item.evidence_sha256;
  const pills=byId("detailPills");clear(pills);
  addPill(pills,item.category);addPill(pills,item.status);
  if(item.immutable)addPill(pills,"IMMUTABLE");
  const meta=byId("detailMeta");clear(meta);
  const rows=[
    ["Occurred",formatDate(item.occurred_at)],
    ["Kind",item.kind],
    ["Source",item.source_table],
    ["Source ID",item.source_id],
    ["Parent ID",item.parent_id||"—"],
    ["Actor",item.actor||"—"],
    ["Evidence SHA",item.evidence_sha256||"—"],
    ["Severity",item.severity||"INFO"],
  ];
  rows.forEach(([key,value])=>{
    meta.append(el("dt","",key),el("dd",key==="Evidence SHA"?"sha":"",value));
  });
  byId("detailJson").textContent=JSON.stringify(item.details||{},null,2);
}
function selectEvent(item){
  state.selected=item;
  renderTimeline();
  renderDetail(item);
}
async function load(reset=true){
  if(reset){
    state.offset=0;
    state.events=[];
    state.selected=null;
    renderDetail(null);
  }
  byId("refreshButton").disabled=true;
  byId("applyButton").disabled=true;
  byId("loadMoreButton").disabled=true;
  try{
    const p=buildParams(true);
    const data=await api("/v1/shrimp-animation/audit-evidence?"+p.toString());
    if(reset)state.events=data.events||[];
    else state.events=state.events.concat(data.events||[]);
    state.hasMore=Boolean(data.pagination?.has_more);
    if(!reset)state.offset+=data.events?.length||0;
    else state.offset=data.events?.length||0;
    renderSummary(data);
    renderTimeline();
    byId("loadMoreButton").classList.toggle("hidden",!state.hasMore);
  }catch(err){
    byId("resultBadge").textContent="ERROR";
    byId("resultBadge").className="pill bad";
    toast("Evidence Explorer 载入失败："+err.message,true);
  }finally{
    byId("refreshButton").disabled=false;
    byId("applyButton").disabled=false;
    byId("loadMoreButton").disabled=false;
  }
}
function exportEvidence(format){
  const p=buildParams(false);
  p.set("format",format);
  window.location.href="/v1/shrimp-animation/audit-evidence/export?"+p.toString();
}
byId("refreshButton").addEventListener("click",()=>load(true));
byId("applyButton").addEventListener("click",()=>load(true));
byId("resetButton").addEventListener("click",()=>{
  ["categoryFilter","statusFilter","queryFilter","afterFilter","beforeFilter"].forEach(id=>{byId(id).value="";});
  load(true);
});
byId("loadMoreButton").addEventListener("click",()=>load(false));
byId("exportJsonButton").addEventListener("click",()=>exportEvidence("json"));
byId("exportCsvButton").addEventListener("click",()=>exportEvidence("csv"));
byId("copyShaButton").addEventListener("click",async()=>{
  const sha=state.selected?.evidence_sha256;
  if(!sha)return;
  try{await navigator.clipboard.writeText(sha);toast("Evidence SHA 已复制");}
  catch(_){toast("浏览器不允许复制",true);}
});
byId("queryFilter").addEventListener("keydown",(event)=>{
  if(event.key==="Enter")load(true);
});
(function hydrateFromUrl(){
  const p=new URLSearchParams(window.location.search);
  const pairs=[
    ["category","categoryFilter"],
    ["status","statusFilter"],
    ["q","queryFilter"],
  ];
  pairs.forEach(([key,id])=>{
    const value=p.get(key);
    if(value)byId(id).dataset.initialValue=value;
  });
  if(p.get("q"))byId("queryFilter").value=p.get("q");
})();
load(true).then(()=>{
  ["categoryFilter","statusFilter"].forEach(id=>{
    const node=byId(id);
    const value=node.dataset.initialValue;
    if(value&&Array.from(node.options).some(option=>option.value===value)){
      node.value=value;
      delete node.dataset.initialValue;
      load(true);
    }
  });
});
