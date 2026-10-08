"use strict";

const state={targets:[],episodes:[],selectedJob:null,plans:[]};
const byId=(id)=>document.getElementById(id);
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;}
function clear(n){n.replaceChildren();}
function short(v){if(!v)return "—";return String(v).length>22?String(v).slice(0,12)+"…"+String(v).slice(-8):String(v);}
function pill(text,good=true){return el("span","pill "+(good?"good":"warn"),text);}
function toast(msg,bad=false){const n=byId("toast");n.textContent=msg;n.className="toast"+(bad?" bad":"");n.classList.remove("hidden");clearTimeout(toast.t);toast.t=setTimeout(()=>n.classList.add("hidden"),4200);}
async function api(url,opt={}){
  const r=await fetch(url,{cache:"no-store",headers:{"Accept":"application/json",...(opt.headers||{})},...opt});
  const type=r.headers.get("content-type")||"";
  const body=type.includes("application/json")?await r.json():await r.text();
  if(!r.ok){const detail=body&&body.detail?body.detail:body;throw new Error(typeof detail==="string"?detail:JSON.stringify(detail));}
  return body;
}
function deepJob(){
  const p="/animation-publishing/";
  return location.pathname.startsWith(p)?location.pathname.slice(p.length):"";
}
function targetOption(t){
  const o=document.createElement("option");
  o.value=t.target_key;
  o.textContent=t.platform+" · "+t.display_name+" · "+t.target_key;
  return o;
}
async function loadTargets(){
  state.targets=await api("/v1/shrimp-animation/publish-targets?active_only=true");
  const list=byId("targetList");clear(list);
  if(!state.targets.length)list.appendChild(el("div","muted","尚未注册 Platform Target。"));
  state.targets.forEach(t=>{
    const card=el("div","target-card");
    const head=el("div","plan-head");
    head.append(el("strong","",t.display_name),pill(t.platform,true));
    card.append(head,el("div","muted",t.target_key+(t.account_reference?" · "+t.account_reference:"")));
    list.appendChild(card);
  });
  const select=byId("planTarget");clear(select);
  state.targets.forEach(t=>select.appendChild(targetOption(t)));
}
async function loadEpisodes(){
  const all=await api("/v1/shrimp-animation/review-workspace?limit=200");
  state.episodes=all.filter(x=>x.review_status==="RELEASE_APPROVED");
  const list=byId("episodeList");clear(list);
  if(!state.episodes.length){list.appendChild(el("div","muted","目前没有 RELEASE_APPROVED Episode。"));return;}
  state.episodes.forEach(item=>{
    const card=el("div","episode-card");
    if(item.job_id===state.selectedJob)card.classList.add("active");
    card.append(
      el("strong","",item.title||item.episode_id),
      el("div","muted",item.episode_id),
      el("div","hashline","bundle "+short(item.episode_bundle_sha256))
    );
    card.addEventListener("click",()=>selectEpisode(item.job_id,true));
    list.appendChild(card);
  });
  const deep=deepJob();
  const target=deep||state.selectedJob;
  if(target&&state.episodes.some(x=>x.job_id===target))await selectEpisode(target,false);
  else if(!state.selectedJob&&state.episodes.length)await selectEpisode(state.episodes[0].job_id,false);
}
async function selectEpisode(jobId,push){
  state.selectedJob=jobId;
  if(push)history.pushState({jobId},"","/animation-publishing/"+jobId);
  const e=state.episodes.find(x=>x.job_id===jobId);
  byId("planEmpty").classList.add("hidden");
  byId("planForm").classList.remove("hidden");
  const selected=byId("selectedEpisode");clear(selected);
  selected.append(
    el("strong","",e?.title||e?.episode_id||jobId),
    el("div","muted",jobId),
    pill("RELEASE_APPROVED",true)
  );
  if(!byId("planTitle").value&&e?.title)byId("planTitle").value=e.title;
  renderEpisodesOnly();
  await Promise.all([loadPlans(),loadExecutionEvidence()]);
}
function renderEpisodesOnly(){
  const list=byId("episodeList");clear(list);
  state.episodes.forEach(item=>{
    const card=el("div","episode-card"+(item.job_id===state.selectedJob?" active":""));
    card.append(
      el("strong","",item.title||item.episode_id),
      el("div","muted",item.episode_id),
      el("div","hashline","bundle "+short(item.episode_bundle_sha256))
    );
    card.addEventListener("click",()=>selectEpisode(item.job_id,true));
    list.appendChild(card);
  });
}
function dryItem(label,value){
  const n=el("div","dry-item");
  n.append(el("strong","",label),el("div","safe-zero",value));
  return n;
}
function renderPlan(plan){
  const card=el("div","plan-card");
  const head=el("div","plan-head");
  head.append(
    el("div","",plan.platform+" · "+plan.target_key),
    pill(plan.plan_status,plan.plan_status==="PUBLISH_AUTHORIZED"||plan.plan_status==="PENDING_AUTHORIZATION")
  );
  card.appendChild(head);
  const meta=plan.publish_metadata||{};
  card.append(
    el("h3","",meta.title||"Untitled"),
    el("div","muted",(meta.visibility||"—")+" · "+(meta.category||"no category")),
    el("div","hashline","plan "+plan.plan_sha256),
    el("div","hashline","dry-run "+plan.dry_run_sha256)
  );
  const snap=plan.dry_run_snapshot||{};
  const grid=el("div","dry-grid");
  grid.append(
    dryItem("Network requests",String(snap.network_request_count??"—")),
    dryItem("Credential access",String(snap.credential_access_count??"—")),
    dryItem("External writes",String(snap.external_write_count??"—")),
    dryItem("Publish performed",String(Boolean(snap.publish_performed)))
  );
  card.appendChild(grid);

  const checks=el("div","muted","");
  checks.style.marginTop="8px";
  checks.textContent=(snap.checks||[]).map(x=>(x.passed?"✓ ":"✕ ")+x.key).join(" · ");
  card.appendChild(checks);

  if((plan.decisions||[]).length){
    const d=plan.decisions[0];
    card.appendChild(el("div","selection-card",
      "Decision: "+d.decision+" · "+d.actor+" · "+short(d.decision_sha256)
    ));
  }

  if(plan.plan_status==="PENDING_AUTHORIZATION"){
    const box=el("div","decision-box-mini");
    const reason=document.createElement("input");
    reason.placeholder="授权/拒绝理由";
    const key=document.createElement("input");
    key.type="password";key.autocomplete="off";key.placeholder="X-Shrimp-Publish-Key";
    const auth=el("button","button approve","AUTHORIZE");
    const reject=el("button","button danger","REJECT");
    auth.type="button";reject.type="button";
    async function decide(decision){
      if(reason.value.trim().length<3){toast("理由至少 3 个字符",true);return;}
      if(!key.value){toast("请输入发布授权密钥",true);return;}
      if(decision==="AUTHORIZE"&&!confirm("确认将这个 dry-run Plan 标记为 PUBLISH_AUTHORIZED？\n\n不会上传或发布任何内容。"))return;
      auth.disabled=true;reject.disabled=true;
      try{
        const result=await api("/v1/shrimp-animation/publish-plans/"+plan.id+"/decision",{
          method:"POST",
          headers:{"Content-Type":"application/json","X-Shrimp-Publish-Key":key.value},
          body:JSON.stringify({
            decision,
            reason:reason.value.trim(),
            actor:"shrimp-publish-ui",
            plan_sha256:plan.plan_sha256,
            dry_run_sha256:plan.dry_run_sha256
          })
        });
        key.value="";reason.value="";
        toast(result.plan_status+"；external publish 仍为 DISABLED");
        await Promise.all([loadPlans(),loadExecutionEvidence()]);
      }catch(err){key.value="";toast("决策失败："+err.message,true);auth.disabled=false;reject.disabled=false;}
    }
    auth.addEventListener("click",()=>decide("AUTHORIZE"));
    reject.addEventListener("click",()=>decide("REJECT"));
    box.append(reason,key,auth,reject);
    card.appendChild(box);
  }
  return card;
}
function evidenceRow(title,status,meta){
  const card=el("div","execution-evidence-card");
  const head=el("div","plan-head");
  head.append(el("strong","",title),pill(status,["CURRENT","PUBLISHED","CLEANED_UP","SNAPSHOT_CREATED","READY"].includes(status)));
  card.append(head,el("div","muted",meta||""));
  return card;
}
async function loadExecutionEvidence(){
  const list=byId("executionList"),safety=byId("executionSafety");
  clear(list);clear(safety);
  if(!state.selectedJob){
    list.appendChild(el("div","muted","请选择 Episode。"));
    return;
  }
  const data=await api("/v1/shrimp-animation/jobs/"+state.selectedJob+"/pipeline-console");
  const safe=data.safety||{};
  safety.append(
    dryItem("External side effects",safe.external_side_effects||"—"),
    dryItem("Production execution",String(Boolean(safe.production_execution_enabled))),
    dryItem("Provider publish flag",String(Boolean(safe.publish_enabled))),
    dryItem("Console writes",String(Boolean(safe.console_write_actions)))
  );
  const executions=data.publish_executions||[];
  if(!executions.length){
    list.appendChild(el("div","muted","尚无 Controlled Publisher Execution。"));
  }
  executions.forEach(x=>{
    list.appendChild(evidenceRow(
      (x.platform||"—")+" · "+(x.target_key||"—"),
      x.execution_status||"UNKNOWN",
      "execution "+short(x.id)+" · upload "+(x.upload_outcome||"—")+" "+(x.upload_write_count??0)+"/1 · publish "+(x.publish_outcome||"—")+" "+(x.publish_write_count??0)+"/1 · source "+(x.source_stale?"STALE":"CURRENT")
    ));
  });
  (data.bilibili_acceptances||[]).forEach(x=>{
    list.appendChild(evidenceRow(
      "Bilibili Provider Read-Back",
      x.acceptance_status||"UNKNOWN",
      "read-back "+Boolean(x.provider_read_back_verified)+" · private "+Boolean(x.private_visibility_verified)+" · cleanup "+Boolean(x.cleanup_verified)+" · production touched "+Boolean(x.production_account_touched)
    ));
  });
}
async function loadPlans(){
  const list=byId("planList");clear(list);
  if(!state.selectedJob){list.appendChild(el("div","muted","请选择 Episode。"));return;}
  const base=await api("/v1/shrimp-animation/jobs/"+state.selectedJob+"/publish-plans");
  state.plans=[];
  for(const p of base){
    state.plans.push(await api("/v1/shrimp-animation/publish-plans/"+p.id));
  }
  if(!state.plans.length){list.appendChild(el("div","muted","尚未生成 Publisher Plan。"));return;}
  state.plans.forEach(p=>list.appendChild(renderPlan(p)));
}

byId("registerTargetButton").addEventListener("click",async()=>{
  const key=byId("targetKeySecret");
  if(!key.value){toast("请输入发布授权密钥",true);return;}
  try{
    await api("/v1/shrimp-animation/publish-targets",{
      method:"POST",
      headers:{"Content-Type":"application/json","X-Shrimp-Publish-Key":key.value},
      body:JSON.stringify({
        target_key:byId("targetKey").value.trim(),
        platform:byId("targetPlatform").value,
        display_name:byId("targetName").value.trim(),
        account_reference:byId("targetAccount").value.trim()||null,
        metadata_constraints:{},
        actor:"shrimp-publish-ui"
      })
    });
    key.value="";toast("Platform Target 已注册；未保存任何凭证。");await loadTargets();
  }catch(err){key.value="";toast("注册失败："+err.message,true);}
});

byId("createPlanButton").addEventListener("click",async()=>{
  if(!state.selectedJob){toast("请选择 Episode",true);return;}
  if(!byId("planTarget").value){toast("请先注册 Platform Target",true);return;}
  const key=byId("planKeySecret");
  if(!key.value){toast("请输入发布授权密钥",true);return;}
  const tags=byId("planTags").value.split(",").map(x=>x.trim()).filter(Boolean);
  try{
    const result=await api("/v1/shrimp-animation/jobs/"+state.selectedJob+"/publish-plans",{
      method:"POST",
      headers:{"Content-Type":"application/json","X-Shrimp-Publish-Key":key.value},
      body:JSON.stringify({
        target_key:byId("planTarget").value,
        publish_metadata:{
          title:byId("planTitle").value.trim(),
          description:byId("planDescription").value.trim(),
          tags,
          category:byId("planCategory").value.trim()||null,
          visibility:byId("planVisibility").value
        },
        actor:"shrimp-publish-ui"
      })
    });
    key.value="";
    toast("dry-run VERIFIED："+short(result.dry_run_sha256));
    await Promise.all([loadPlans(),loadExecutionEvidence()]);
  }catch(err){key.value="";toast("Plan 创建失败："+err.message,true);}
});

byId("refreshButton").addEventListener("click",async()=>{
  await Promise.all([loadTargets(),loadEpisodes()]);
  if(state.selectedJob)await Promise.all([loadPlans(),loadExecutionEvidence()]);
  toast("已重新整理");
});
window.addEventListener("popstate",async()=>{
  const j=deepJob();
  if(j&&state.episodes.some(x=>x.job_id===j))await selectEpisode(j,false);
});

(async()=>{
  try{
    await loadTargets();
    await loadEpisodes();
  }catch(err){toast("载入失败："+err.message,true);}
})();
