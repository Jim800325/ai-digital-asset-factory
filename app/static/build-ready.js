"use strict";
const state={proposals:[],selected:null,detail:null,decisions:[],authorization:null,readiness:null};

const $=id=>document.getElementById(id);
const setText=(id,value)=>{$(id).textContent=value??"—";};
const clearChildren=node=>{while(node.firstChild)node.removeChild(node.firstChild);};
const statusClass=(el,value)=>{
  el.className="soft-status";
  const v=String(value||"").toUpperCase();
  if(["APPROVED","AUTHORIZED","READY","BUILD_READY"].includes(v))el.classList.add("ready");
  else if(["REJECTED","BLOCKED","STALE"].includes(v))el.classList.add("blocked");
};

async function api(path,options={}){
  const response=await fetch(path,{cache:"no-store",...options});
  let body=null;
  const text=await response.text();
  try{body=text?JSON.parse(text):null;}catch{body={detail:text||response.statusText};}
  if(!response.ok)throw new Error(body?.detail||body?.message||("HTTP "+response.status));
  return body;
}

function appendList(id,items){
  const list=$(id); clearChildren(list);
  const values=Array.isArray(items)?items:[];
  if(!values.length){const li=document.createElement("li");li.textContent="—";list.appendChild(li);return;}
  values.forEach(value=>{const li=document.createElement("li");li.textContent=String(value);list.appendChild(li);});
}

function renderProposalList(){
  const root=$("proposalList"); clearChildren(root);
  if(!state.proposals.length){const p=document.createElement("p");p.className="empty";p.textContent="目前沒有 Build Proposal。";root.appendChild(p);return;}
  state.proposals.forEach(item=>{
    const button=document.createElement("button");
    button.type="button";
    button.className="proposal-card"+(state.selected===item.id?" active":"");
    const status=document.createElement("span");status.textContent=item.proposal_status;
    const title=document.createElement("strong");title.textContent=item.opportunity_title||item.title;
    const meta=document.createElement("small");meta.textContent=(item.artifact_type||"—")+" · r"+item.revision;
    button.append(status,title,meta);
    button.addEventListener("click",()=>selectProposal(item.id));
    root.appendChild(button);
  });
}

function renderDetail(){
  const d=state.detail;
  if(!d)return;
  setText("proposalTitle",d.opportunity_title||d.title);
  setText("proposalId",d.id);
  setText("proposalRevision","r"+d.revision);
  setText("artifactType",d.artifact_type);
  setText("validationScore",d.research_validation_score);
  setText("proposalObjective",d.objective);
  setText("proposalStatus",d.proposal_status);
  statusClass($("proposalStatus"),d.proposal_status);
  appendList("successCriteria",d.success_criteria);
  appendList("constraints",d.constraints);
  const policy=d.sandbox_policy||{};
  setText("policyNetwork",policy.network||"—");
  setText("policyDeployment",policy.deployment||"—");
  setText("policySideEffects",policy.external_side_effects||"—");
  setText("policyHumanRelease",policy.human_release_required===true?"REQUIRED":"—");

  const pending=d.proposal_status==="PENDING_APPROVAL";
  $("approveProposalButton").disabled=!pending||!$("approvalConfirm").checked;
  $("rejectProposalButton").disabled=!pending||!$("approvalConfirm").checked;
  setText("approvalGateBadge",pending?"ACTION REQUIRED":d.proposal_status);
  statusClass($("approvalGateBadge"),d.proposal_status);
  setText("approvalMessage",pending?"輸入 Approval Key、理由並確認安全邊界。":"Proposal 已是 "+d.proposal_status+"；決策不可在同一 Revision 重放。");
}

function renderAuthorization(){
  const d=state.detail, r=state.readiness, a=state.authorization;
  if(!d){return;}
  const approved=d.proposal_status==="APPROVED";
  if(a){
    setText("authorizationBadge",a.authorization_status);
    setText("authorizationGateState",a.authorization_status);
    setText("sourceState","SNAPSHOT LOCKED");
    setText("executorState",a.executor_kind||"—");
    setText("sandboxExecutionState","NOT STARTED");
    setText("authorizationMessage","授權記錄已固定："+a.authorization_status+"。本頁沒有 Execute 動作。");
    statusClass($("authorizationBadge"),a.authorization_status);
  }else if(r){
    setText("authorizationBadge",r.status);
    setText("authorizationGateState",r.status);
    setText("sourceState",r.source_current?"CURRENT":"BLOCKED");
    setText("executorState",r.executor_kind||"—");
    setText("sandboxExecutionState",r.safety?.sandbox_execution_enabled?"ENABLED / NOT STARTED":"DISABLED");
    const blockers=(r.blockers||[]).join(", ");
    setText("authorizationMessage",r.status==="READY"?"Gate 已 READY；可做第二次顯式人類授權。":"阻塞："+(blockers||"等待 Gate 1"));
    statusClass($("authorizationBadge"),r.status);
  }else{
    setText("authorizationBadge",approved?"CHECKING":"LOCKED");
    setText("authorizationGateState",approved?"CHECKING":"LOCKED");
    setText("sourceState","—");
    setText("sandboxExecutionState","DISABLED");
  }

  const canDecide=approved && r?.status==="READY" && !a && $("authorizationConfirm").checked;
  $("authorizeButton").disabled=!canDecide;
  $("rejectAuthorizationButton").disabled=!canDecide;
}

function renderAudit(){
  const root=$("auditList");clearChildren(root);
  const entries=[];
  state.decisions.forEach(d=>entries.push({
    label:"Proposal "+d.decision,
    text:d.reason,
    meta:(d.actor||"human")+" · "+(d.decided_at||"")
  }));
  if(state.authorization)entries.push({
    label:"Sandbox "+state.authorization.authorization_status,
    text:state.authorization.reason,
    meta:(state.authorization.actor||"human")+" · "+(state.authorization.authorized_at||state.authorization.rejected_at||state.authorization.created_at||"")
  });
  if(!entries.length){const p=document.createElement("p");p.className="empty";p.textContent="尚未有決策記錄。";root.appendChild(p);return;}
  entries.forEach(entry=>{
    const card=document.createElement("div");card.className="audit-entry";
    const strong=document.createElement("strong");strong.textContent=entry.label;
    const span=document.createElement("span");span.textContent=entry.text||"—";
    const small=document.createElement("small");small.textContent=entry.meta;
    card.append(strong,span,small);root.appendChild(card);
  });
}

async function loadAuthorization(proposalId){
  state.authorization=null;state.readiness=null;
  try{state.authorization=await api("/v1/build-authorizations/"+proposalId);}catch(error){
    if(!String(error.message).includes("not found"))console.warn(error);
  }
  const executor=encodeURIComponent($("executorKind").value);
  try{state.readiness=await api("/v1/build-authorizations/"+proposalId+"/readiness?executor_kind="+executor);}catch(error){
    setText("authorizationMessage","Readiness 讀取失敗："+error.message);
  }
  renderAuthorization();renderAudit();
}

async function selectProposal(id){
  state.selected=id;renderProposalList();
  state.detail=await api("/v1/build-proposals/"+id);
  state.decisions=await api("/v1/build-proposals/"+id+"/decisions");
  renderDetail();renderAudit();
  await loadAuthorization(id);
}

async function loadProposals(){
  try{
    state.proposals=await api("/v1/build-proposals?limit=50");
    renderProposalList();
    const preferred=state.proposals.find(x=>x.proposal_status==="PENDING_APPROVAL"||x.proposal_status==="APPROVED")||state.proposals[0];
    if(preferred)await selectProposal(preferred.id);
  }catch(error){
    const root=$("proposalList");clearChildren(root);
    const p=document.createElement("p");p.className="empty";p.textContent="讀取失敗："+error.message;root.appendChild(p);
  }
}

async function decideProposal(decision){
  if(!state.detail)return;
  try{
    setText("approvalMessage","正在提交 "+decision+"…");
    await api("/v1/build-proposals/"+state.detail.id+"/decision",{
      method:"POST",
      headers:{"content-type":"application/json","X-Approval-Key":$("approvalKey").value},
      body:JSON.stringify({decision,reason:$("approvalReason").value,actor:"sbw4-human-review"})
    });
    $("approvalKey").value="";$("approvalReason").value="";$("approvalConfirm").checked=false;
    await loadProposals();
  }catch(error){setText("approvalMessage",error.message);}
}

async function decideAuthorization(decision){
  if(!state.detail)return;
  try{
    setText("authorizationMessage","正在提交 "+decision+"…");
    state.authorization=await api("/v1/build-authorizations/"+state.detail.id+"/decision",{
      method:"POST",
      headers:{"content-type":"application/json","X-Build-Authorization-Key":$("authorizationKey").value},
      body:JSON.stringify({
        decision,
        reason:$("authorizationReason").value,
        actor:"sbw4-human-build-authorizer",
        executor_kind:$("executorKind").value
      })
    });
    $("authorizationKey").value="";$("authorizationReason").value="";$("authorizationConfirm").checked=false;
    await loadAuthorization(state.detail.id);
  }catch(error){setText("authorizationMessage",error.message);}
}

$("refreshButton").addEventListener("click",loadProposals);
$("approvalConfirm").addEventListener("change",renderDetail);
$("authorizationConfirm").addEventListener("change",renderAuthorization);
$("executorKind").addEventListener("change",()=>{if(state.detail)loadAuthorization(state.detail.id);});
$("approveProposalButton").addEventListener("click",()=>decideProposal("APPROVE"));
$("rejectProposalButton").addEventListener("click",()=>decideProposal("REJECT"));
$("authorizeButton").addEventListener("click",()=>decideAuthorization("AUTHORIZE"));
$("rejectAuthorizationButton").addEventListener("click",()=>decideAuthorization("REJECT"));

loadProposals();
