"use strict";
const byId=id=>document.getElementById(id);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n};
const clear=n=>n.replaceChildren();
const state={summary:null,targets:[],accounts:[],slots:[],selection:null,editing:null,quota:null,reservations:[],claims:[],ledger:[],audits:[],operations:null,circuitEvents:[],incidents:[],approvals:[],notifications:[],incidentOps:null,oncallRoutes:[],slaEvents:[],pirs:[],correctiveActions:[],reliability:null,reliabilityTrend:null,governance:null};
function toast(msg,bad=false){const n=byId("toast");n.textContent=msg;n.className="toast"+(bad?" bad":"");n.classList.remove("hidden");clearTimeout(toast.t);toast.t=setTimeout(()=>n.classList.add("hidden"),3200)}
async function api(url,opts={}){const r=await fetch(url,{cache:"no-store",...opts,headers:{"Accept":"application/json",...(opts.headers||{})}});const b=await r.json().catch(()=>({detail:"Invalid response"}));if(!r.ok)throw new Error(b.detail||("HTTP "+r.status));return b}
function pill(v){const s=String(v||"UNKNOWN").toUpperCase();const cls=/READY|SUCCESS|APPROVED|AUTHORIZED|PUBLISHED|CURRENT|ACTIVE|PASS|CONFIGURED/.test(s)?"good":/BLOCKED|FAILED|REJECTED|STALE|DISABLED|MISSING|INACTIVE|EXPIRED|UNHEALTHY|MISMATCH|LOGGED_OUT|DENIED/.test(s)?"bad":"warn";return el("span","pill "+cls,s)}
function row(root,key,value){const n=el("div","setting-row");n.append(el("span","setting-key",key),el("span","setting-value",value));root.appendChild(n)}
function empty(tbody,cols,msg){const tr=document.createElement("tr"),td=el("td","empty-row",msg);td.colSpan=cols;tr.appendChild(td);tbody.appendChild(tr)}
function currentPage(){const p=location.pathname;return p.includes("/accounts")?"accounts":p.includes("/jobs")?"jobs":p.includes("/executions")?"executions":p.includes("/quota")?"quota":p.includes("/operations")?"operations":p.includes("/reliability-review")?"governance":p.includes("/reliability")?"reliability":"settings"}
const meta={
 accounts:["Bilibili 账号管理","多账号 Registry、默认投稿配置、发布窗口、每日限额与账号级安全策略。"],
 jobs:["动画任务中心","查看 Shrimp Animation pipeline 任务并快速进入 Review / Publishing。"],
 executions:["发布执行中心","查看受控 Upload / Publish、Exactly-once write budget 与 stale 状态。"],
 quota:["额度运营中心","查看账号日额度、Reservation / Claim 生命周期、Stuck Claim reconciliation 与 Daily Reset Audit。"],
 operations:["Publisher Operations Console","Claim Escalation、Automatic Recovery Policy 与 Account Circuit Breaker。"],
 reliability:["Reliability Scorecard","30 天 SLO / Error Budget / MTTR / Ambiguity / Circuit / Root Cause recurrence。"],
 governance:["Reliability Governance Review","人工审查 NORMAL / CAUTION / FREEZE_RECOMMENDED，并生成不可执行 Policy Intent。"],
 settings:["增强设置","集中查看运行环境、发布器、Bilibili 与 Gate 配置；敏感值永不回显。"]
};
function showPage(){const p=currentPage();document.querySelectorAll(".admin-page").forEach(x=>x.classList.add("hidden"));byId(p+"Page").classList.remove("hidden");document.querySelectorAll(".admin-nav [data-page]").forEach(a=>a.classList.toggle("active",a.dataset.page===p));byId("pageTitle").textContent=meta[p][0];byId("pageSubtitle").textContent=meta[p][1]}
function csv(value){return String(value||"").split(",").map(x=>x.trim()).filter(Boolean)}
function timeText(v){return v?String(v).slice(0,5):"全天"}
function findAccount(ref){const value=String(ref||"").replace(/^MID:/i,"");return state.accounts.find(a=>a.account_key===ref||a.mid===value)}
function renderRegistry(){
  const body=byId("registryRows");clear(body);
  if(!state.accounts.length){empty(body,10,"尚未创建 Bilibili Account Profile");return}
  state.accounts.forEach(a=>{
    const tr=document.createElement("tr");
    const title=el("td","",a.display_name||a.account_key);title.append(el("div","muted",a.account_key));tr.appendChild(title);
    tr.append(el("td","mono",a.mid||"—"));
    const status=document.createElement("td");status.appendChild(pill(a.account_status));tr.appendChild(status);
    tr.append(el("td","",Array.isArray(a.tags)?a.tags.join(" · "):"—"));
    tr.append(el("td","",a.default_tid??"—"),el("td","",a.default_copyright||"—"),el("td","",a.daily_publish_limit??"—"));
    tr.append(el("td","",(a.publish_window_start&&a.publish_window_end)?timeText(a.publish_window_start)+"–"+timeText(a.publish_window_end):"全天"));
    tr.append(el("td","",a.safety_policy?.mode||"—"));
    const ops=document.createElement("td");
    const edit=el("button","button ghost small","编辑");edit.type="button";edit.addEventListener("click",()=>beginEdit(a));
    const toggle=el("button","button ghost small",a.account_status==="ACTIVE"?"停用":"启用");toggle.type="button";toggle.addEventListener("click",()=>toggleAccount(a));
    ops.append(edit,toggle);tr.appendChild(ops);
    body.appendChild(tr);
  });
}
function renderCredentialSlots(){
  const body=byId("credentialRows");clear(body);
  if(!state.slots.length){empty(body,10,"尚未绑定 Credential Slot");}
  state.slots.forEach(slot=>{
    const tr=document.createElement("tr");
    tr.append(el("td","",slot.display_name||slot.account_key||"—"),el("td","mono",slot.slot_key||"—"),el("td","mono",slot.env_prefix||"—"));
    const cred=document.createElement("td");cred.appendChild(pill(slot.credential_status||"UNKNOWN"));tr.appendChild(cred);
    const login=document.createElement("td");login.appendChild(pill(slot.login_status||"UNKNOWN"));tr.appendChild(login);
    const mid=document.createElement("td");mid.appendChild(pill(slot.mid_status||"UNKNOWN"));tr.appendChild(mid);
    const perm=document.createElement("td");perm.appendChild(pill(slot.publish_permission_status||"UNKNOWN"));tr.appendChild(perm);
    const health=document.createElement("td");health.appendChild(pill(slot.health_status||"UNKNOWN"));tr.appendChild(health);
    tr.append(el("td","",slot.last_checked_at?new Date(slot.last_checked_at).toLocaleString():"—"));
    const ops=document.createElement("td");
    const probe=el("button","button ghost small","健康检查");probe.type="button";probe.addEventListener("click",()=>probeSlot(slot));
    ops.appendChild(probe);tr.appendChild(ops);body.appendChild(tr);
  });
  const box=byId("healthySelection");
  if(state.selection){
    box.className="notice";
    box.textContent="已选择："+state.selection.display_name+" · MID "+state.selection.mid+" · Slot "+state.selection.credential_slot_key+" · HEALTHY";
  }else{
    box.className="notice warn";
    box.textContent="当前没有可自动选择的 HEALTHY SACRIFICIAL 账号。";
  }
}
async function probeSlot(slot){
  const key=prompt("输入 Step 10B Live Acceptance Key 执行只读健康检查：");
  if(!key)return;
  try{
    const result=await api("/v1/shrimp-animation/bilibili-credential-slots/"+encodeURIComponent(slot.slot_key)+"/health-check",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Live-Acceptance-Key":key},body:JSON.stringify({actor:"shrimp-control-center-v0.2"})});
    toast("健康检查完成："+result.health_status);
    await load();
  }catch(e){toast("健康检查失败："+e.message,true)}
}
function renderAccounts(){
  const s=state.summary||{},ready=s.bilibili?.readiness||{},checks=ready.checks||{};
  byId("readinessBadge").textContent=ready.status||"UNKNOWN";
  const old=byId("credentialBadge"),fresh=pill(checks.bilibili_cookie_credentials_present?"CONFIGURED":"MISSING");fresh.id="credentialBadge";old.replaceWith(fresh);
  const metrics=byId("accountMetrics");clear(metrics);
  const active=state.accounts.filter(x=>x.account_status==="ACTIVE").length;
  [["Registry",state.accounts.length],["Active Accounts",active],["Bilibili Targets",state.targets.filter(x=>x.platform==="BILIBILI").length],["Cookie Bundle",checks.bilibili_cookie_credentials_present?"CONFIGURED":"MISSING"]].forEach(([k,v])=>{const card=el("div","metric-card");card.append(el("div","metric-label",k),el("div","metric-value small",v));metrics.appendChild(card)});
  const creds=byId("credentialChecks");clear(creds);
  [["SESSDATA + bili_jct + MID",checks.bilibili_cookie_credentials_present],["Step 9 Authorization Gate",checks.publish_authorization_gate],["Step 10 Execution Gate",checks.publish_execution_gate],["Step 10B Live Gate",checks.bilibili_live_acceptance_gate]].forEach(([k,v])=>{const n=el("div","check-item");n.append(el("span","",k),pill(v?"PASS":"BLOCKED"));creds.appendChild(n)});
  const policies=byId("policyChecks");clear(policies);
  [["牺牲账号 allowlist",checks.sacrificial_account_allowlist_present],["真实账号 denylist",checks.real_account_denylist_present],["测试 target allowlist",checks.sacrificial_target_allowlist_present],["真实 target denylist",checks.real_target_denylist_present],["Allow / deny 不重叠",checks.allowlist_denylist_disjoint]].forEach(([k,v])=>{const n=el("div","check-item");n.append(el("span","",k),pill(v?"PASS":"BLOCKED"));policies.appendChild(n)});
  const body=byId("accountRows");clear(body);const rows=state.targets.filter(x=>x.platform==="BILIBILI");
  if(!rows.length)empty(body,5,"尚未注册 Bilibili Publish Target");
  rows.forEach(x=>{const tr=document.createElement("tr"),account=findAccount(x.account_reference);tr.append(el("td","",x.display_name||"—"),el("td","mono",x.account_reference||"—"),el("td","mono",x.target_key||"—"));const st=document.createElement("td");st.appendChild(pill(x.target_status||"UNKNOWN"));tr.appendChild(st);tr.append(el("td","",account?(account.display_name+" · "+account.account_status):"UNREGISTERED"));body.appendChild(tr)});
  renderRegistry();
  renderCredentialSlots();
}
function beginEdit(a){
  state.editing=a.account_key;byId("accountKey").value=a.account_key;byId("accountKey").disabled=true;byId("accountDisplayName").value=a.display_name||"";byId("accountMid").value=a.mid||"";byId("accountMid").disabled=true;byId("accountTags").value=(a.tags||[]).join(", ");byId("accountDefaultTid").value=a.default_tid||122;byId("accountCopyright").value=a.default_copyright||"ORIGINAL";byId("accountDescription").value=a.default_description||"";byId("accountDefaultTags").value=(a.default_tags||[]).join(", ");byId("accountCoverStrategy").value=a.cover_strategy||"REQUIRE_ARTIFACT";byId("accountDailyLimit").value=a.daily_publish_limit??1;byId("accountWindowStart").value=a.publish_window_start?String(a.publish_window_start).slice(0,5):"";byId("accountWindowEnd").value=a.publish_window_end?String(a.publish_window_end).slice(0,5):"";byId("accountTimezone").value=a.timezone||"Asia/Shanghai";byId("accountSafetyMode").value=a.safety_policy?.mode||"SACRIFICIAL";byId("accountAllowPublic").checked=Boolean(a.safety_policy?.allow_public_visibility);byId("accountSaveButton").textContent="保存修改";byId("accountCancelEdit").classList.remove("hidden");byId("accountDisplayName").focus()
}
function resetAccountForm(){
  state.editing=null;byId("accountForm").reset();byId("accountKey").disabled=false;byId("accountMid").disabled=false;byId("accountDefaultTid").value=122;byId("accountCopyright").value="ORIGINAL";byId("accountCoverStrategy").value="REQUIRE_ARTIFACT";byId("accountDailyLimit").value=1;byId("accountTimezone").value="Asia/Shanghai";byId("accountSafetyMode").value="SACRIFICIAL";byId("accountSaveButton").textContent="创建账号";byId("accountCancelEdit").classList.add("hidden")
}
async function toggleAccount(a){
  const key=prompt("输入 Step 9 Authorization Key 以"+(a.account_status==="ACTIVE"?"停用":"启用")+"该账号：");
  if(!key)return;
  try{await api("/v1/shrimp-animation/bilibili-accounts/"+encodeURIComponent(a.account_key),{method:"PATCH",headers:{"Content-Type":"application/json","X-Shrimp-Publish-Key":key},body:JSON.stringify({account_status:a.account_status==="ACTIVE"?"INACTIVE":"ACTIVE",actor:"shrimp-control-center-v0.2"})});toast("账号状态已更新");await load()}catch(e){toast("更新失败："+e.message,true)}
}
function renderJobs(){const rows=state.summary?.pipeline?.recent_jobs||[],body=byId("jobRows");clear(body);byId("jobsCount").textContent=(state.summary?.pipeline?.total_jobs||0)+" JOBS";if(!rows.length){empty(body,5,"尚无 Shrimp Animation Job");return}rows.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.id||"—"),el("td","",x.current_stage||"—"));const st=document.createElement("td");st.appendChild(pill(x.job_status));tr.appendChild(st);tr.append(el("td","",x.updated_at?new Date(x.updated_at).toLocaleString():"—"));const open=document.createElement("td"),a=el("a","table-link","Review");a.href="/animation-review/"+x.id;open.appendChild(a);tr.appendChild(open);body.appendChild(tr)})}
function renderExecutions(){const rows=state.summary?.publishing?.recent_executions||[],body=byId("executionRows");clear(body);if(!rows.length){empty(body,7,"尚无 Controlled Publisher Execution");return}rows.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","",x.platform||"—"),el("td","mono",x.target_key||"—"));const st=document.createElement("td");st.appendChild(pill(x.execution_status));tr.appendChild(st);tr.append(el("td","",(x.upload_outcome||"—")+" · "+(x.upload_write_count??0)+"/1"),el("td","",(x.publish_outcome||"—")+" · "+(x.publish_write_count??0)+"/1"));const src=document.createElement("td");src.appendChild(pill(x.source_stale?"STALE":"CURRENT"));tr.appendChild(src);const open=document.createElement("td"),a=el("a","table-link","Publishing");a.href="/animation-publishing/"+x.provider_job_id;open.appendChild(a);tr.appendChild(open);body.appendChild(tr)})}
function renderQuota(){
  const q=state.quota||{},summary=q.reservations_summary||{},accounts=q.accounts||[],stuck=q.stuck_claims||[];
  byId("quotaStuckBadge").textContent=(q.stuck_claim_count||0)+" STUCK";
  byId("stuckThresholdLabel").textContent=">"+(q.stuck_threshold_minutes||0)+" MIN";
  const metrics=byId("quotaMetrics");clear(metrics);
  [["Published Today",summary.published_today||0],["Held",summary.held||0],["Claimed",summary.claimed||0],["Available",summary.available||0]].forEach(([k,v])=>{const card=el("div","metric-card");card.append(el("div","metric-label",k),el("div","metric-value small",v));metrics.appendChild(card)});

  const accountBody=byId("quotaAccountRows");clear(accountBody);
  if(!accounts.length)empty(accountBody,7,"暂无 Active Bilibili Account quota");
  accounts.forEach(a=>{const tr=document.createElement("tr");tr.append(el("td","mono",a.account_key),el("td","",a.local_quota_date||"—"),el("td","",a.daily_publish_limit??0),el("td","",a.published_units??0),el("td","",a.held_units??0),el("td","",a.claimed_units??0),el("td","",a.available_units??0));accountBody.appendChild(tr)});

  const stuckBody=byId("stuckClaimRows");clear(stuckBody);
  if(!stuck.length)empty(stuckBody,8,"当前没有 Stuck Claim");
  stuck.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.account_key||"—"),el("td","mono",x.target_key||"—"),el("td","mono",String(x.execution_id||"").slice(0,12)));const st=document.createElement("td");st.appendChild(pill(x.execution_status));tr.appendChild(st);tr.append(el("td","",x.claimed_at?new Date(x.claimed_at).toLocaleString():"—"),el("td","",(x.upload_write_count??0)+"/1 · "+(x.publish_write_count??0)+"/1"),el("td","",x.recommended_action||"—"));const ops=document.createElement("td");if(["UPLOAD_READBACK","PUBLISH_READBACK"].includes(x.recommended_action)){const b=el("button","button ghost small","Read-back Reconcile");b.type="button";b.addEventListener("click",()=>reconcileStuck(x));ops.appendChild(b)}else{ops.appendChild(el("span","muted","Manual review"))}tr.appendChild(ops);stuckBody.appendChild(tr)});

  const resBody=byId("reservationRows");clear(resBody);
  if(!state.reservations.length)empty(resBody,4,"暂无 Reservation");
  state.reservations.slice(0,40).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.account_key||"—"),el("td","mono",x.target_key||"—"));const st=document.createElement("td");st.appendChild(pill(x.reservation_status));tr.appendChild(st);tr.append(el("td","",x.expires_at?new Date(x.expires_at).toLocaleString():"—"));resBody.appendChild(tr)});

  const claimBody=byId("claimRows");clear(claimBody);
  if(!state.claims.length)empty(claimBody,4,"暂无 Execution Claim");
  state.claims.slice(0,40).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.account_key||"—"),el("td","mono",x.target_key||"—"));const st=document.createElement("td");st.appendChild(pill(x.claim_status));tr.appendChild(st);tr.append(el("td","",x.claimed_at?new Date(x.claimed_at).toLocaleString():"—"));claimBody.appendChild(tr)});

  const auditBody=byId("dailyAuditRows");clear(auditBody);
  if(!state.audits.length)empty(auditBody,8,"尚无 Daily Reset Audit");
  state.audits.slice(0,60).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.account_key||"—"),el("td","",x.local_quota_date||"—"));const st=document.createElement("td");st.appendChild(pill(x.audit_status));tr.appendChild(st);tr.append(el("td","",x.previous_day_published_units??0),el("td","",x.published_units??0),el("td","",x.carryover_claim_count??0),el("td","",x.carryover_reservation_count??0),el("td","",x.available_units??0));auditBody.appendChild(tr)});

  const ledgerBody=byId("quotaLedgerRows");clear(ledgerBody);
  if(!state.ledger.length)empty(ledgerBody,6,"尚无 Quota Ledger");
  state.ledger.slice(0,100).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","",x.created_at?new Date(x.created_at).toLocaleString():"—"),el("td","mono",x.account_key||"—"),el("td","",x.entry_type||"—"),el("td","",x.quota_units??0),el("td","",x.local_quota_date||"—"),el("td","mono",String(x.entry_sha256||"").slice(0,12)+"…"));ledgerBody.appendChild(tr)});
}
async function reconcileStuck(x){
  const key=prompt("输入 Step 10B Live Acceptance Key，仅执行 provider read-back reconciliation：");
  if(!key)return;
  try{
    const result=await api("/v1/shrimp-animation/bilibili-stuck-claims/"+encodeURIComponent(x.execution_id)+"/reconcile",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Live-Acceptance-Key":key},body:JSON.stringify({actor:"shrimp-control-center-v0.3"})});
    toast("Reconcile: "+result.reconciliation_outcome);
    await load();
  }catch(e){toast("Reconcile 失败："+e.message,true)}
}
async function requestIncidentRecovery(x){
  const key=prompt("输入 Step 10B Live Acceptance Key，创建 Recovery Review 请求：");
  if(!key)return;
  try{
    await api("/v1/shrimp-animation/bilibili-incidents/"+encodeURIComponent(x.id)+"/recovery-request",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Live-Acceptance-Key":key},body:JSON.stringify({actor:"shrimp-control-center-v0.4"})});
    toast("Recovery Review 已创建");await load();
  }catch(e){toast("Recovery Request 失败："+e.message,true)}
}
async function decideRecovery(x,decision){
  const key=prompt("输入独立 Recovery Approval Key：");
  if(!key)return;
  const reason=prompt(decision+" 原因：","Evidence reviewed in Publisher Operations Console");
  if(!reason)return;
  try{
    await api("/v1/shrimp-animation/bilibili-recovery-approvals/"+encodeURIComponent(x.id)+"/decision",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Recovery-Approval-Key":key},body:JSON.stringify({decision,reason,actor:"shrimp-control-center-v0.4"})});
    toast("Recovery "+decision+" 已记录");await load();
  }catch(e){toast("Recovery Decision 失败："+e.message,true)}
}
async function applyRecovery(x){
  const key=prompt("输入 Step 10B Live Acceptance Key，应用已批准恢复：");
  if(!key)return;
  try{
    await api("/v1/shrimp-animation/bilibili-recovery-approvals/"+encodeURIComponent(x.id)+"/apply",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Live-Acceptance-Key":key},body:JSON.stringify({actor:"shrimp-control-center-v0.4"})});
    toast("Approved Recovery 已应用");await load();
  }catch(e){toast("Recovery Apply 失败："+e.message,true)}
}
async function showIncidentTimeline(x){
  try{
    const rows=await api("/v1/shrimp-animation/bilibili-incidents/"+encodeURIComponent(x.id)+"/timeline");
    const lines=(rows||[]).map(r=>(r.created_at?new Date(r.created_at).toLocaleString():"")+" · "+r.event_type+" · "+(r.source_type||""));
    alert(lines.join("\n")||"暂无 Timeline Event");
  }catch(e){toast("Timeline 读取失败："+e.message,true)}
}
async function acknowledgeIncident(x){
  const key=prompt("输入独立 Incident Ops Key：");
  if(!key)return;
  try{
    await api("/v1/shrimp-animation/bilibili-incidents/"+encodeURIComponent(x.id)+"/acknowledge",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Incident-Ops-Key":key},body:JSON.stringify({actor:"shrimp-control-center-v0.5"})});
    toast("Incident 已 ACK");await load();
  }catch(e){toast("ACK 失败："+e.message,true)}
}
async function assignIncidentOwner(x){
  const owner=prompt("新的 Owner：",x.owner_ref||"");
  if(!owner)return;
  const key=prompt("输入独立 Incident Ops Key：");
  if(!key)return;
  try{
    await api("/v1/shrimp-animation/bilibili-incidents/"+encodeURIComponent(x.id)+"/owner",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Incident-Ops-Key":key},body:JSON.stringify({owner_ref:owner,actor:"shrimp-control-center-v0.5"})});
    toast("Owner 已更新");await load();
  }catch(e){toast("Owner 更新失败："+e.message,true)}
}
async function completePir(x){
  const root=prompt("Root Cause：",x.root_cause||"");
  if(!root)return;
  const lessons=prompt("Lessons Learned：",x.lessons_learned||"");
  if(!lessons)return;
  const key=prompt("输入独立 Incident Ops Key：");
  if(!key)return;
  try{
    await api("/v1/shrimp-animation/bilibili-incidents/"+encodeURIComponent(x.incident_id)+"/pir/complete",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Incident-Ops-Key":key},body:JSON.stringify({root_cause:root,lessons_learned:lessons,actor:"shrimp-control-center-v0.5"})});
    toast("PIR 已完成");await load();
  }catch(e){toast("PIR 完成失败："+e.message,true)}
}
async function addCorrectiveActionForPir(x){
  const desc=prompt("Corrective Action：");
  if(!desc)return;
  const owner=prompt("Action Owner：",x.owner_ref||"publisher-oncall");
  if(!owner)return;
  const due=prompt("Due At (ISO，可留空)：","");
  const key=prompt("输入独立 Incident Ops Key：");
  if(!key)return;
  try{
    await api("/v1/shrimp-animation/bilibili-incidents/"+encodeURIComponent(x.incident_id)+"/corrective-actions",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Incident-Ops-Key":key},body:JSON.stringify({description:desc,owner_ref:owner,due_at:due||null,actor:"shrimp-control-center-v0.5"})});
    toast("Corrective Action 已添加");await load();
  }catch(e){toast("Action 添加失败："+e.message,true)}
}
async function configureOncallRoute(x){
  const owner=prompt("Primary Owner：",x.owner_ref||"");
  if(!owner)return;
  const backup=prompt("Backup Owner：",x.secondary_owner_ref||"");
  const key=prompt("输入独立 Incident Ops Key：");
  if(!key)return;
  try{
    await api("/v1/shrimp-animation/bilibili-oncall-routes",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Incident-Ops-Key":key},body:JSON.stringify({severity:x.severity,owner_ref:owner,secondary_owner_ref:backup||null,actor:"shrimp-control-center-v0.5"})});
    toast("On-Call Route 已更新");await load();
  }catch(e){toast("Route 更新失败："+e.message,true)}
}
async function completeCorrectiveAction(x){
  const evidence=prompt("Completion Evidence：",x.completion_evidence||"");
  if(!evidence)return;
  const key=prompt("输入独立 Incident Ops Key：");
  if(!key)return;
  try{
    await api("/v1/shrimp-animation/bilibili-corrective-actions/"+encodeURIComponent(x.id)+"/complete",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Incident-Ops-Key":key},body:JSON.stringify({completion_evidence:evidence,actor:"shrimp-control-center-v0.5"})});
    toast("Corrective Action 已完成");await load();
  }catch(e){toast("Action 完成失败："+e.message,true)}
}
function renderOperations(){
  const ops=state.operations||{},summary=ops.summary||{},escalations=ops.open_escalations||[],circuits=ops.circuits||[],runs=ops.recovery_runs||[];
  const incidentBody=byId("incidentRows");clear(incidentBody);
  if(!state.incidents.length)empty(incidentBody,10,"当前没有 Publisher Incident");
  state.incidents.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.incident_key||String(x.id).slice(0,12)),el("td","mono",x.account_key||"—"));const sev=document.createElement("td");sev.appendChild(pill(x.severity));tr.appendChild(sev);const st=document.createElement("td");st.appendChild(pill(x.incident_status));tr.appendChild(st);const ack=document.createElement("td");ack.appendChild(pill(x.acknowledgement_status||"UNACKNOWLEDGED"));tr.appendChild(ack);tr.append(el("td","",x.owner_ref||"—"));const sla=document.createElement("td");sla.appendChild(pill(x.sla_status||"WITHIN_SLA"));tr.appendChild(sla);const pir=document.createElement("td");pir.appendChild(pill(x.pir_status||"NOT_REQUIRED"));tr.appendChild(pir);tr.append(el("td","",x.recovery_due_at?new Date(x.recovery_due_at).toLocaleString():"—"));const opsCell=document.createElement("td");const timeline=el("button","button ghost small","Timeline");timeline.type="button";timeline.addEventListener("click",()=>showIncidentTimeline(x));opsCell.appendChild(timeline);if(x.acknowledgement_status!=="ACKNOWLEDGED"&&x.incident_status!=="RESOLVED"){const ackBtn=el("button","button ghost small","ACK");ackBtn.type="button";ackBtn.addEventListener("click",()=>acknowledgeIncident(x));opsCell.appendChild(ackBtn)}if(x.incident_status!=="RESOLVED"){const ownerBtn=el("button","button ghost small","Owner");ownerBtn.type="button";ownerBtn.addEventListener("click",()=>assignIncidentOwner(x));opsCell.appendChild(ownerBtn)}if(x.incident_status==="OPEN"){const req=el("button","button ghost small","Request Recovery");req.type="button";req.addEventListener("click",()=>requestIncidentRecovery(x));opsCell.appendChild(req)}tr.appendChild(opsCell);incidentBody.appendChild(tr)});

  const routeBody=byId("oncallRouteRows");clear(routeBody);
  if(!state.oncallRoutes.length)empty(routeBody,5,"尚无 On-Call Route");
  state.oncallRoutes.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","",x.severity||"—"),el("td","",x.owner_ref||"—"),el("td","",x.secondary_owner_ref||"—"));const st=document.createElement("td");st.appendChild(pill(x.route_status));tr.appendChild(st);const opsCell=document.createElement("td");const b=el("button","button ghost small","Edit Route");b.type="button";b.addEventListener("click",()=>configureOncallRoute(x));opsCell.appendChild(b);tr.appendChild(opsCell);routeBody.appendChild(tr)});

  const slaBody=byId("incidentSlaRows");clear(slaBody);
  if(!state.slaEvents.length)empty(slaBody,5,"尚无 SLA Event");
  state.slaEvents.slice(0,80).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.incident_key||"—"),el("td","",x.event_type||"—"),el("td","",(x.previous_status||"—")+" → "+(x.next_status||"—")),el("td","",x.due_at?new Date(x.due_at).toLocaleString():"—"),el("td","",x.observed_at?new Date(x.observed_at).toLocaleString():"—"));slaBody.appendChild(tr)});

  const pirBody=byId("pirRows");clear(pirBody);
  if(!state.pirs.length)empty(pirBody,5,"尚无 PIR");
  state.pirs.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.incident_key||"—"));const st=document.createElement("td");st.appendChild(pill(x.review_status));tr.appendChild(st);tr.append(el("td","",x.owner_ref||"—"),el("td","",x.completed_at?new Date(x.completed_at).toLocaleString():"—"));const opsCell=document.createElement("td");if(x.review_status!=="COMPLETED"){const done=el("button","button ghost small","Complete PIR");done.type="button";done.addEventListener("click",()=>completePir(x));opsCell.appendChild(done)}const add=el("button","button ghost small","Add Action");add.type="button";add.addEventListener("click",()=>addCorrectiveActionForPir(x));opsCell.appendChild(add);tr.appendChild(opsCell);pirBody.appendChild(tr)});

  const caBody=byId("correctiveActionRows");clear(caBody);
  if(!state.correctiveActions.length)empty(caBody,6,"尚无 Corrective Action");
  state.correctiveActions.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.incident_key||"—"),el("td","",x.action_key+" · "+x.description),el("td","",x.owner_ref||"—"));const st=document.createElement("td");st.appendChild(pill(x.action_status));tr.appendChild(st);tr.append(el("td","",x.due_at?new Date(x.due_at).toLocaleString():"—"));const opsCell=document.createElement("td");if(x.action_status!=="COMPLETED"&&x.action_status!=="CANCELLED"){const b=el("button","button ghost small","Complete");b.type="button";b.addEventListener("click",()=>completeCorrectiveAction(x));opsCell.appendChild(b)}else{opsCell.appendChild(el("span","muted","—"))}tr.appendChild(opsCell);caBody.appendChild(tr)});

  const approvalBody=byId("recoveryApprovalRows");clear(approvalBody);
  if(!state.approvals.length)empty(approvalBody,7,"当前没有 Recovery Approval");
  state.approvals.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.incident_key||"—"),el("td","mono",x.account_key||"—"));const st=document.createElement("td");st.appendChild(pill(x.request_status));tr.appendChild(st);tr.append(el("td","",x.requested_at?new Date(x.requested_at).toLocaleString():"—"),el("td","",x.decision||"—"),el("td","mono",String(x.evidence_sha256||"").slice(0,12)+"…"));const opsCell=document.createElement("td");if(x.request_status==="PENDING"){for(const d of ["APPROVE","REJECT"]){const b=el("button","button ghost small",d);b.type="button";b.addEventListener("click",()=>decideRecovery(x,d));opsCell.appendChild(b)}}else if(x.request_status==="APPROVED"){const b=el("button","button ghost small","Apply");b.type="button";b.addEventListener("click",()=>applyRecovery(x));opsCell.appendChild(b)}else{opsCell.appendChild(el("span","muted","—"))}tr.appendChild(opsCell);approvalBody.appendChild(tr)});

  const notificationBody=byId("notificationRows");clear(notificationBody);
  if(!state.notifications.length)empty(notificationBody,7,"当前没有 Operations Notification");
  state.notifications.slice(0,80).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","",x.created_at?new Date(x.created_at).toLocaleString():"—"),el("td","mono",x.incident_key||"—"),el("td","",x.notification_type||"—"));const sev=document.createElement("td");sev.appendChild(pill(x.severity));tr.appendChild(sev);tr.append(el("td","",x.destination_type||"—"));const st=document.createElement("td");st.appendChild(pill(x.delivery_status));tr.appendChild(st);tr.append(el("td","",x.attempt_count??0));notificationBody.appendChild(tr)});

  const io=state.incidentOps||{};
  byId("operationsRiskBadge").textContent=((summary.critical_escalations||0)+(io.sla_breached||0))+" CRITICAL";
  const metrics=byId("operationsMetrics");clear(metrics);
  [["Active Incidents",io.active_incidents||0],["Unacknowledged",io.unacknowledged||0],["SLA Breached",io.sla_breached||0],["PIR Required",io.pir_required||0]].forEach(([k,v])=>{const card=el("div","metric-card");card.append(el("div","metric-label",k),el("div","metric-value small",v));metrics.appendChild(card)});

  const escBody=byId("escalationRows");clear(escBody);
  if(!escalations.length)empty(escBody,8,"当前没有 Open Claim Escalation");
  escalations.forEach(x=>{
    const tr=document.createElement("tr");
    const lv=document.createElement("td");lv.appendChild(pill(x.escalation_level));tr.appendChild(lv);
    tr.append(el("td","mono",x.account_key||"—"),el("td","mono",x.target_key||"—"),el("td","mono",String(x.execution_id||"").slice(0,12)),el("td","",(x.claim_age_minutes??0)+" min"),el("td","",x.ambiguity_count??0));
    const st=document.createElement("td");st.appendChild(pill(x.escalation_status));tr.appendChild(st);
    tr.append(el("td","",x.recommended_action||"—"));
    escBody.appendChild(tr);
  });

  const circuitBody=byId("circuitRows");clear(circuitBody);
  if(!circuits.length)empty(circuitBody,8,"暂无 Active Account Circuit");
  circuits.forEach(x=>{
    const tr=document.createElement("tr");
    tr.append(el("td","mono",x.account_key||"—"));
    const st=document.createElement("td");st.appendChild(pill(x.circuit_status));tr.appendChild(st);
    tr.append(el("td","",x.ambiguity_score??0),el("td","",x.provider_failure_score??0),el("td","",x.open_reason||"—"),el("td","",x.recovery_not_before?new Date(x.recovery_not_before).toLocaleString():"—"),el("td","mono",String(x.last_evidence_sha256||"").slice(0,12)+(x.last_evidence_sha256?"…":"")));
    const opsCell=document.createElement("td");
    if(["OPEN","RECOVERY_PENDING"].includes(x.circuit_status)){
      const b=el("button","button ghost small","Evaluate Evidence");b.type="button";b.addEventListener("click",()=>evaluateCircuit(x));opsCell.appendChild(b)
    }else{opsCell.appendChild(el("span","muted","Router enabled"))}
    tr.appendChild(opsCell);circuitBody.appendChild(tr);
  });

  const runBody=byId("recoveryRunRows");clear(runBody);
  if(!runs.length)empty(runBody,7,"尚无 Automatic Recovery Run");
  runs.slice(0,40).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","",x.started_at?new Date(x.started_at).toLocaleString():"—"));const st=document.createElement("td");st.appendChild(pill(x.run_status));tr.appendChild(st);tr.append(el("td","",x.stuck_claim_count??0),el("td","",x.reconciled_count??0),el("td","",x.still_ambiguous_count??0),el("td","",x.circuits_opened??0),el("td","",x.circuits_closed??0));runBody.appendChild(tr)});

  const eventBody=byId("circuitEventRows");clear(eventBody);
  if(!state.circuitEvents.length)empty(eventBody,5,"尚无 Circuit Event");
  state.circuitEvents.slice(0,60).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","",x.created_at?new Date(x.created_at).toLocaleString():"—"),el("td","mono",x.account_key||"—"),el("td","",x.event_type||"—"),el("td","",(x.previous_status||"—")+" → "+(x.next_status||"—")),el("td","",x.reason||"—"));eventBody.appendChild(tr)});
}
async function evaluateCircuit(x){
  const key=prompt("输入 Step 10B Live Acceptance Key，仅评估健康/read-back证据，不执行发布：");
  if(!key)return;
  try{
    const result=await api("/v1/shrimp-animation/bilibili-accounts/"+encodeURIComponent(x.account_key)+"/circuit/evaluate",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Live-Acceptance-Key":key},body:JSON.stringify({actor:"shrimp-control-center-v0.3"})});
    toast("Circuit: "+result.circuit_status);
    await load();
  }catch(e){toast("Circuit 评估失败："+e.message,true)}
}
function renderReliability(){
  const data=state.reliability||{},g=data.latest_global||{},payload=g.metrics_payload||{},ack=payload.ack_slo||{},recovery=payload.recovery_slo||{};
  const badge=byId("reliabilityGradeBadge");
  badge.textContent=g.reliability_grade?("GRADE "+g.reliability_grade):"NO DATA";
  badge.className="pill "+(g.reliability_grade==="A"?"good":g.reliability_grade==="F"?"bad":"warn");

  const metrics=byId("reliabilityMetrics");clear(metrics);
  [
    ["Score",g.reliability_score??"—"],
    ["Avg ACK",g.avg_ack_minutes==null?"—":g.avg_ack_minutes+" min"],
    ["P95 MTTR",g.p95_mttr_minutes==null?"—":g.p95_mttr_minutes+" min"],
    ["Ambiguity",g.ambiguity_rate_percent==null?"—":g.ambiguity_rate_percent+"%"],
    ["Circuit Opens",g.circuit_open_count??0],
    ["Recurring Causes",g.recurring_root_cause_count??0]
  ].forEach(([k,v])=>{const card=el("div","metric-card");card.append(el("div","metric-label",k),el("div","metric-value small",v));metrics.appendChild(card)});

  const sloBody=byId("sloBudgetRows");clear(sloBody);
  const rows=[
    ["ACK",ack.target_percent,ack.success_rate,ack.allowed,ack.consumed,ack.remaining],
    ["Recovery",recovery.target_percent,recovery.success_rate,recovery.allowed,recovery.consumed,recovery.remaining]
  ];
  if(!g.id)empty(sloBody,6,"尚无 Reliability Scorecard");
  else rows.forEach(r=>{const tr=document.createElement("tr");tr.append(el("td","",r[0]),el("td","",r[1]+"%"),el("td","",r[2]+"%"),el("td","",r[3]),el("td","",r[4]),el("td","",r[5]));sloBody.appendChild(tr)});

  const scoreBody=byId("reliabilityScorecardRows");clear(scoreBody);
  const scorecards=Array.isArray(data.scorecards)?data.scorecards:[];
  if(!scorecards.length)empty(scoreBody,9,"尚无 Scorecard Snapshot");
  scorecards.slice(0,80).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.scope_type==="GLOBAL"?"GLOBAL":(x.account_key||"ACCOUNT")),el("td","",x.reliability_score??"—"));const gr=document.createElement("td");gr.appendChild(pill(x.reliability_grade||"—"));tr.appendChild(gr);tr.append(el("td","",x.incident_count??0),el("td","",x.ack_slo_breach_count??0),el("td","",x.recovery_slo_breach_count??0),el("td","",x.circuit_open_count??0),el("td","",(x.ambiguity_rate_percent??0)+"%"),el("td","",x.generated_at?new Date(x.generated_at).toLocaleString():"—"));scoreBody.appendChild(tr)});

  const trendData=state.reliabilityTrend||{};
  const trendBody=byId("reliabilityTrendRows");clear(trendBody);
  const trendPoints=Array.isArray(trendData.trend_points)?trendData.trend_points:[];
  if(!trendPoints.length)empty(trendBody,8,"尚无 Reliability Trend");
  trendPoints.slice(0,120).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","",x.bucket_type+" · "+(x.bucket_start?new Date(x.bucket_start).toLocaleDateString():"—")),el("td","mono",x.scope_type==="GLOBAL"?"GLOBAL":(x.account_key||"ACCOUNT")),el("td","",x.reliability_score??"—"),el("td","",(x.ack_success_rate??0)+"%"),el("td","",(x.recovery_success_rate??0)+"%"),el("td","",(x.ambiguity_rate_percent??0)+"%"),el("td","",x.circuit_open_count??0),el("td","",x.recurring_root_cause_count??0));trendBody.appendChild(tr)});

  const burnBody=byId("burnRateRows");clear(burnBody);
  const burns=Array.isArray(trendData.burn_evaluations)?trendData.burn_evaluations:[];
  if(!burns.length)empty(burnBody,7,"尚无 Burn Rate Evaluation");
  burns.slice(0,80).forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.scope_type==="GLOBAL"?"GLOBAL":(x.account_key||"ACCOUNT")));const st=document.createElement("td");st.appendChild(pill(x.burn_status));tr.appendChild(st);tr.append(el("td","",x.ack_short_burn_rate??0),el("td","",x.ack_long_burn_rate??0),el("td","",x.recovery_short_burn_rate??0),el("td","",x.recovery_long_burn_rate??0),el("td","",x.evaluated_at?new Date(x.evaluated_at).toLocaleString():"—"));burnBody.appendChild(tr)});

  const regBody=byId("regressionRows");clear(regBody);
  const regressions=Array.isArray(trendData.open_regressions)?trendData.open_regressions:[];
  if(!regressions.length)empty(regBody,7,"当前没有 Reliability Regression");
  regressions.forEach(x=>{const tr=document.createElement("tr");const sev=document.createElement("td");sev.appendChild(pill(x.severity));tr.appendChild(sev);tr.append(el("td","mono",x.scope_type==="GLOBAL"?"GLOBAL":(x.account_key||"ACCOUNT")),el("td","",x.metric_key||"—"),el("td","",x.baseline_value??"—"),el("td","",x.current_value??"—"),el("td","",x.absolute_delta??"—"),el("td","",x.detected_at?new Date(x.detected_at).toLocaleString():"—"));regBody.appendChild(tr)});

  const policyBody=byId("policyRecommendationRows");clear(policyBody);
  const recommendations=Array.isArray(trendData.recommendations)?trendData.recommendations:[];
  if(!recommendations.length)empty(policyBody,5,"当前没有 Reliability Policy Recommendation");
  recommendations.forEach(x=>{const tr=document.createElement("tr");const p=document.createElement("td");p.appendChild(pill(x.priority));tr.appendChild(p);tr.append(el("td","",x.category||"—"),el("td","",x.title||"—"),el("td","",x.recommendation_text||"—"),el("td","",x.generated_at?new Date(x.generated_at).toLocaleString():"—"));policyBody.appendChild(tr)});

  const recurrenceBody=byId("recurrenceRows");clear(recurrenceBody);
  const clusters=Array.isArray(data.recurrence_clusters)?data.recurrence_clusters:[];
  if(!clusters.length)empty(recurrenceBody,8,"尚无已完成 PIR 的 Root Cause recurrence");
  clusters.forEach(x=>{const tr=document.createElement("tr");const st=document.createElement("td");st.appendChild(pill(x.recurrence_status));tr.appendChild(st);tr.append(el("td","",x.normalized_root_cause||"—"),el("td","",x.occurrence_count??0),el("td","",x.critical_occurrence_count??0),el("td","",Array.isArray(x.account_keys)?x.account_keys.join(" · "):"—"),el("td","",x.first_observed_at?new Date(x.first_observed_at).toLocaleString():"—"),el("td","",x.last_observed_at?new Date(x.last_observed_at).toLocaleString():"—"),el("td","mono",String(x.root_cause_fingerprint||"").slice(0,12)+"…"));recurrenceBody.appendChild(tr)});
}

async function decideGovernance(review,decision){
  const key=prompt("输入独立 Reliability Governance Key：");
  if(!key)return;
  const reason=prompt("Human Policy Decision 原因：","Reviewed reliability evidence snapshot");
  if(!reason)return;
  try{
    await api("/v1/shrimp-animation/bilibili-reliability-governance/reviews/"+encodeURIComponent(review.id)+"/decision",{
      method:"POST",
      headers:{"Content-Type":"application/json","X-Shrimp-Bilibili-Reliability-Governance-Key":key},
      body:JSON.stringify({decision,reason,actor:"shrimp-control-center-governance-v0.1"})
    });
    toast("Governance Decision 已记录；未执行任何策略变更");
    await load();
  }catch(e){toast("Governance Decision 失败："+e.message,true)}
}

function renderGovernance(){
  const data=state.governance||{},review=data.current_review||null;
  const badge=byId("governanceRecommendationBadge");
  badge.textContent=review?.recommendation||"NO REVIEW";
  badge.className="pill "+(review?.recommendation==="NORMAL"?"good":review?.recommendation==="FREEZE_RECOMMENDED"?"bad":"warn");

  const metrics=byId("governanceMetrics");clear(metrics);
  [
    ["Human Gate",data.human_gate_required?"REQUIRED":"UNKNOWN"],
    ["Execution Supported",data.execution_supported?"YES":"NO"],
    ["Changes Applied",data.changes_applied?"YES":"NO"],
    ["Review Status",review?.review_status||"NONE"]
  ].forEach(([k,v])=>{const card=el("div","metric-card");card.append(el("div","metric-label",k),el("div","metric-value small",v));metrics.appendChild(card)});

  const current=byId("governanceCurrentReview");clear(current);
  if(!review){
    row(current,"Current Review","尚未生成 Governance Review");
  }else{
    row(current,"Review Key",review.review_key||"—");
    row(current,"Recommendation",review.recommendation||"—");
    row(current,"Reason",review.recommendation_reason||"—");
    row(current,"Status",review.review_status||"—");
    row(current,"Evidence SHA",review.evidence_sha256||"—");
    row(current,"Generated",review.generated_at?new Date(review.generated_at).toLocaleString():"—");
  }

  const actions=byId("governanceDecisionActions");clear(actions);
  if(review&&review.review_status==="PENDING_DECISION"){
    const allowed=review.recommendation==="NORMAL"
      ?["ACCEPT_NORMAL","REJECT_RECOMMENDATION"]
      :review.recommendation==="CAUTION"
        ?["ACCEPT_CAUTION","REJECT_RECOMMENDATION"]
        :["AUTHORIZE_FREEZE_INTENT","REJECT_RECOMMENDATION"];
    allowed.forEach(decision=>{
      const b=el("button","button "+(decision==="AUTHORIZE_FREEZE_INTENT"?"danger":"ghost"),decision);
      b.type="button";
      b.addEventListener("click",()=>decideGovernance(review,decision));
      actions.appendChild(b);
    });
  }else{
    actions.appendChild(el("span","muted","当前没有可决策的 Governance Review"));
  }

  const evidence=byId("governanceEvidence");clear(evidence);
  const snap=review?.evidence_snapshot||{},score=snap.scorecard||{},burn=snap.burn||{};
  row(evidence,"Reliability Score",score.reliability_score??"—");
  row(evidence,"Grade",score.reliability_grade||"—");
  row(evidence,"Burn Status",burn.burn_status||"—");
  row(evidence,"ACK Burn Short / Long",(burn.ack_short_burn_rate??"—")+" / "+(burn.ack_long_burn_rate??"—"));
  row(evidence,"Recovery Burn Short / Long",(burn.recovery_short_burn_rate??"—")+" / "+(burn.recovery_long_burn_rate??"—"));
  row(evidence,"Open Regressions",Array.isArray(snap.open_regressions)?snap.open_regressions.length:0);
  row(evidence,"Recurrence Signals",Array.isArray(snap.recurrence_clusters)?snap.recurrence_clusters.length:0);
  row(evidence,"Policy Recommendations",Array.isArray(snap.policy_recommendations)?snap.policy_recommendations.length:0);

  const reviewBody=byId("governanceReviewRows");clear(reviewBody);
  const reviews=Array.isArray(data.reviews)?data.reviews:[];
  if(!reviews.length)empty(reviewBody,5,"尚无 Governance Review");
  reviews.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.review_key||String(x.id).slice(0,12)));const rec=document.createElement("td");rec.appendChild(pill(x.recommendation));tr.appendChild(rec);const st=document.createElement("td");st.appendChild(pill(x.review_status));tr.appendChild(st);tr.append(el("td","mono",String(x.evidence_sha256||"").slice(0,12)+"…"),el("td","",x.generated_at?new Date(x.generated_at).toLocaleString():"—"));reviewBody.appendChild(tr)});

  const decisionBody=byId("governanceDecisionRows");clear(decisionBody);
  const decisions=Array.isArray(data.decisions)?data.decisions:[];
  if(!decisions.length)empty(decisionBody,6,"尚无 Human Policy Decision");
  decisions.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.review_key||"—"));const rec=document.createElement("td");rec.appendChild(pill(x.recommendation));tr.appendChild(rec);const dec=document.createElement("td");dec.appendChild(pill(x.decision));tr.appendChild(dec);tr.append(el("td","",x.actor||"—"),el("td","",x.reason||"—"),el("td","",x.decided_at?new Date(x.decided_at).toLocaleString():"—"));decisionBody.appendChild(tr)});

  const intentBody=byId("governanceIntentRows");clear(intentBody);
  const intents=Array.isArray(data.policy_intents)?data.policy_intents:[];
  if(!intents.length)empty(intentBody,6,"尚无 Policy Intent");
  intents.forEach(x=>{const tr=document.createElement("tr");tr.append(el("td","mono",x.review_key||"—"),el("td","",x.intent_type||"—"));const st=document.createElement("td");st.appendChild(pill(x.intent_status));tr.appendChild(st);tr.append(el("td","",x.execution_enabled?"ENABLED":"DISABLED"),el("td","",x.changes_applied?"YES":"NO"),el("td","",x.authorized_at?new Date(x.authorized_at).toLocaleString():"—"));intentBody.appendChild(tr)});
}

function renderSettings(){const s=state.summary||{},sys=s.system||{},r=s.bilibili?.readiness||{},c=r.checks||{};const groups=[["runtimeSettings",[["Vercel Env",sys.vercel_env||"—"],["Database",sys.database_available?"AVAILABLE":"UNAVAILABLE"],["Database Source",sys.database_source||"—"],["Preview Isolated",sys.preview_isolated?"YES":"NO"],["Migrations",sys.migration_status||"—"]]],["publisherSettings",[["Adapter",sys.publisher_adapter||"—"],["Executor",c.publish_executor_enabled?"ENABLED":"DISABLED"],["Step 9 Gate",c.publish_authorization_gate?"READY":"BLOCKED"],["Step 10 Gate",c.publish_execution_gate?"READY":"BLOCKED"]]],["bilibiliSettings",[["Account Profiles",state.accounts.length],["Controlled Adapter",c.bilibili_controlled_adapter?"READY":"BLOCKED"],["Live Acceptance",c.bilibili_live_acceptance_enabled?"ENABLED":"DISABLED"],["Cookie Bundle",c.bilibili_cookie_credentials_present?"CONFIGURED":"MISSING"]]],["securitySettings",[["Secrets Redacted",r.secrets_redacted?"YES":"NO"],["Account Allowlist",c.sacrificial_account_allowlist_present?"SET":"MISSING"],["Account Denylist",c.real_account_denylist_present?"SET":"MISSING"],["Target Allowlist",c.sacrificial_target_allowlist_present?"SET":"MISSING"],["Target Denylist",c.real_target_denylist_present?"SET":"MISSING"]]]];groups.forEach(([id,items])=>{const root=byId(id);clear(root);items.forEach(([k,v])=>row(root,k,v))})}
async function load(){byId("refreshButton").disabled=true;try{
  const [summary,targets,accounts,slots,quota,reservations,claims,ledger,audits,operations,circuitEvents,incidents,approvals,notifications,incidentOps,oncallRoutes,slaEvents,pirs,correctiveActions,reliability,reliabilityTrend,governance]=await Promise.all([
    api("/v1/shrimp-animation/control-center/summary"),
    api("/v1/shrimp-animation/publish-targets"),
    api("/v1/shrimp-animation/bilibili-accounts"),
    api("/v1/shrimp-animation/bilibili-credential-slots"),
    api("/v1/shrimp-animation/bilibili-quota-dashboard"),
    api("/v1/shrimp-animation/bilibili-reservations?limit=100"),
    api("/v1/shrimp-animation/bilibili-execution-claims?limit=100"),
    api("/v1/shrimp-animation/bilibili-quota-ledger?limit=100"),
    api("/v1/shrimp-animation/bilibili-daily-quota-audits?limit=100"),
    api("/v1/shrimp-animation/bilibili-operations-console"),
    api("/v1/shrimp-animation/bilibili-circuit-events?limit=100"),
    api("/v1/shrimp-animation/bilibili-incidents?limit=100"),
    api("/v1/shrimp-animation/bilibili-recovery-approvals?limit=100"),
    api("/v1/shrimp-animation/bilibili-notifications?limit=100"),
    api("/v1/shrimp-animation/bilibili-incident-ops-summary"),
    api("/v1/shrimp-animation/bilibili-oncall-routes?limit=100"),
    api("/v1/shrimp-animation/bilibili-incident-sla-events?limit=100"),
    api("/v1/shrimp-animation/bilibili-post-incident-reviews?limit=100"),
    api("/v1/shrimp-animation/bilibili-corrective-actions?limit=100"),
    api("/v1/shrimp-animation/bilibili-reliability-dashboard"),
    api("/v1/shrimp-animation/bilibili-reliability-trend-dashboard"),
    api("/v1/shrimp-animation/bilibili-reliability-governance")
  ]);
  let selection=null;
  try{selection=await api("/v1/shrimp-animation/bilibili-account-selection/healthy")}catch(_){}
  state.summary=summary;state.targets=Array.isArray(targets)?targets:[];state.accounts=Array.isArray(accounts)?accounts:[];state.slots=Array.isArray(slots)?slots:[];state.selection=selection;state.quota=quota||{};state.reservations=Array.isArray(reservations)?reservations:[];state.claims=Array.isArray(claims)?claims:[];state.ledger=Array.isArray(ledger)?ledger:[];state.audits=Array.isArray(audits)?audits:[];state.operations=operations||{};state.circuitEvents=Array.isArray(circuitEvents)?circuitEvents:[];state.incidents=Array.isArray(incidents)?incidents:[];state.approvals=Array.isArray(approvals)?approvals:[];state.notifications=Array.isArray(notifications)?notifications:[];state.incidentOps=incidentOps||{};state.oncallRoutes=Array.isArray(oncallRoutes)?oncallRoutes:[];state.slaEvents=Array.isArray(slaEvents)?slaEvents:[];state.pirs=Array.isArray(pirs)?pirs:[];state.correctiveActions=Array.isArray(correctiveActions)?correctiveActions:[];state.reliability=reliability||{};state.reliabilityTrend=reliabilityTrend||{};state.governance=governance||{};
  renderAccounts();renderJobs();renderExecutions();renderQuota();renderOperations();renderReliability();renderGovernance();renderSettings();
}catch(e){toast("载入失败："+e.message,true)}finally{byId("refreshButton").disabled=false}}
byId("accountForm").addEventListener("submit",async e=>{
  e.preventDefault();const key=byId("accountPublishKey").value;
  const common={display_name:byId("accountDisplayName").value.trim(),tags:csv(byId("accountTags").value),default_tid:Number(byId("accountDefaultTid").value||122),default_copyright:byId("accountCopyright").value,default_description:byId("accountDescription").value.trim(),default_tags:csv(byId("accountDefaultTags").value),cover_strategy:byId("accountCoverStrategy").value,daily_publish_limit:Number(byId("accountDailyLimit").value||0),publish_window_start:byId("accountWindowStart").value||null,publish_window_end:byId("accountWindowEnd").value||null,timezone:byId("accountTimezone").value.trim()||"Asia/Shanghai",safety_policy:{mode:byId("accountSafetyMode").value,require_global_allowlist:true,allow_public_visibility:byId("accountAllowPublic").checked},actor:"shrimp-control-center-v0.2"};
  try{
    if(state.editing){await api("/v1/shrimp-animation/bilibili-accounts/"+encodeURIComponent(state.editing),{method:"PATCH",headers:{"Content-Type":"application/json","X-Shrimp-Publish-Key":key},body:JSON.stringify(common)});toast("账号档案已更新")}
    else{await api("/v1/shrimp-animation/bilibili-accounts",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Publish-Key":key},body:JSON.stringify({account_key:byId("accountKey").value.trim(),mid:byId("accountMid").value.trim(),...common})});toast("Bilibili 账号档案已创建")}
    byId("accountPublishKey").value="";resetAccountForm();await load()
  }catch(err){byId("accountPublishKey").value="";toast("保存失败："+err.message,true)}
});
byId("accountCancelEdit").addEventListener("click",resetAccountForm);
byId("credentialForm").addEventListener("submit",async e=>{
  e.preventDefault();
  const key=byId("credentialPublishKey").value;
  const payload={
    account_key:byId("credentialAccountKey").value.trim(),
    slot_key:byId("credentialSlotKey").value.trim(),
    env_prefix:byId("credentialEnvPrefix").value.trim().toUpperCase(),
    actor:"shrimp-control-center-v0.2"
  };
  try{
    await api("/v1/shrimp-animation/bilibili-credential-slots",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Publish-Key":key},body:JSON.stringify(payload)});
    byId("credentialPublishKey").value="";
    toast("Credential Slot 已绑定");
    await load();
  }catch(err){
    byId("credentialPublishKey").value="";
    toast("绑定失败："+err.message,true);
  }
});
byId("targetForm").addEventListener("submit",async e=>{e.preventDefault();const key=byId("publishKey").value;const payload={target_key:byId("targetKey").value.trim(),platform:"BILIBILI",display_name:byId("targetDisplayName").value.trim(),account_reference:byId("accountReference").value.trim(),metadata_constraints:{},actor:"shrimp-control-center-v0.2"};try{await api("/v1/shrimp-animation/publish-targets",{method:"POST",headers:{"Content-Type":"application/json","X-Shrimp-Publish-Key":key},body:JSON.stringify(payload)});byId("publishKey").value="";toast("Bilibili Target 已创建");await load()}catch(err){byId("publishKey").value="";toast("创建失败："+err.message,true)}});
byId("refreshButton").addEventListener("click",load);showPage();load();