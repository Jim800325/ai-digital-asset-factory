"use strict";
const byId=id=>document.getElementById(id);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n};
const clear=n=>n.replaceChildren();
const state={summary:null,targets:[],accounts:[],slots:[],selection:null,editing:null,quota:null,reservations:[],claims:[],ledger:[],audits:[]};
function toast(msg,bad=false){const n=byId("toast");n.textContent=msg;n.className="toast"+(bad?" bad":"");n.classList.remove("hidden");clearTimeout(toast.t);toast.t=setTimeout(()=>n.classList.add("hidden"),3200)}
async function api(url,opts={}){const r=await fetch(url,{cache:"no-store",...opts,headers:{"Accept":"application/json",...(opts.headers||{})}});const b=await r.json().catch(()=>({detail:"Invalid response"}));if(!r.ok)throw new Error(b.detail||("HTTP "+r.status));return b}
function pill(v){const s=String(v||"UNKNOWN").toUpperCase();const cls=/READY|SUCCESS|APPROVED|AUTHORIZED|PUBLISHED|CURRENT|ACTIVE|PASS|CONFIGURED/.test(s)?"good":/BLOCKED|FAILED|REJECTED|STALE|DISABLED|MISSING|INACTIVE|EXPIRED|UNHEALTHY|MISMATCH|LOGGED_OUT|DENIED/.test(s)?"bad":"warn";return el("span","pill "+cls,s)}
function row(root,key,value){const n=el("div","setting-row");n.append(el("span","setting-key",key),el("span","setting-value",value));root.appendChild(n)}
function empty(tbody,cols,msg){const tr=document.createElement("tr"),td=el("td","empty-row",msg);td.colSpan=cols;tr.appendChild(td);tbody.appendChild(tr)}
function currentPage(){const p=location.pathname;return p.includes("/accounts")?"accounts":p.includes("/jobs")?"jobs":p.includes("/executions")?"executions":p.includes("/quota")?"quota":"settings"}
const meta={
 accounts:["Bilibili 账号管理","多账号 Registry、默认投稿配置、发布窗口、每日限额与账号级安全策略。"],
 jobs:["动画任务中心","查看 Shrimp Animation pipeline 任务并快速进入 Review / Publishing。"],
 executions:["发布执行中心","查看受控 Upload / Publish、Exactly-once write budget 与 stale 状态。"],
 quota:["额度运营中心","查看账号日额度、Reservation / Claim 生命周期、Stuck Claim reconciliation 与 Daily Reset Audit。"],
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
function renderSettings(){const s=state.summary||{},sys=s.system||{},r=s.bilibili?.readiness||{},c=r.checks||{};const groups=[["runtimeSettings",[["Vercel Env",sys.vercel_env||"—"],["Database",sys.database_available?"AVAILABLE":"UNAVAILABLE"],["Database Source",sys.database_source||"—"],["Preview Isolated",sys.preview_isolated?"YES":"NO"],["Migrations",sys.migration_status||"—"]]],["publisherSettings",[["Adapter",sys.publisher_adapter||"—"],["Executor",c.publish_executor_enabled?"ENABLED":"DISABLED"],["Step 9 Gate",c.publish_authorization_gate?"READY":"BLOCKED"],["Step 10 Gate",c.publish_execution_gate?"READY":"BLOCKED"]]],["bilibiliSettings",[["Account Profiles",state.accounts.length],["Controlled Adapter",c.bilibili_controlled_adapter?"READY":"BLOCKED"],["Live Acceptance",c.bilibili_live_acceptance_enabled?"ENABLED":"DISABLED"],["Cookie Bundle",c.bilibili_cookie_credentials_present?"CONFIGURED":"MISSING"]]],["securitySettings",[["Secrets Redacted",r.secrets_redacted?"YES":"NO"],["Account Allowlist",c.sacrificial_account_allowlist_present?"SET":"MISSING"],["Account Denylist",c.real_account_denylist_present?"SET":"MISSING"],["Target Allowlist",c.sacrificial_target_allowlist_present?"SET":"MISSING"],["Target Denylist",c.real_target_denylist_present?"SET":"MISSING"]]]];groups.forEach(([id,items])=>{const root=byId(id);clear(root);items.forEach(([k,v])=>row(root,k,v))})}
async function load(){byId("refreshButton").disabled=true;try{
  const [summary,targets,accounts,slots,quota,reservations,claims,ledger,audits]=await Promise.all([
    api("/v1/shrimp-animation/control-center/summary"),
    api("/v1/shrimp-animation/publish-targets"),
    api("/v1/shrimp-animation/bilibili-accounts"),
    api("/v1/shrimp-animation/bilibili-credential-slots"),
    api("/v1/shrimp-animation/bilibili-quota-dashboard"),
    api("/v1/shrimp-animation/bilibili-reservations?limit=100"),
    api("/v1/shrimp-animation/bilibili-execution-claims?limit=100"),
    api("/v1/shrimp-animation/bilibili-quota-ledger?limit=100"),
    api("/v1/shrimp-animation/bilibili-daily-quota-audits?limit=100")
  ]);
  let selection=null;
  try{selection=await api("/v1/shrimp-animation/bilibili-account-selection/healthy")}catch(_){}
  state.summary=summary;state.targets=Array.isArray(targets)?targets:[];state.accounts=Array.isArray(accounts)?accounts:[];state.slots=Array.isArray(slots)?slots:[];state.selection=selection;state.quota=quota||{};state.reservations=Array.isArray(reservations)?reservations:[];state.claims=Array.isArray(claims)?claims:[];state.ledger=Array.isArray(ledger)?ledger:[];state.audits=Array.isArray(audits)?audits:[];
  renderAccounts();renderJobs();renderExecutions();renderQuota();renderSettings();
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