"use strict";
const byId=id=>document.getElementById(id);
const state={jobs:[],selected:null,detail:null};
function clear(n){n.replaceChildren()}
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n}
function short(v,n=16){const s=String(v||"");return !s?"—":s.length>n?s.slice(0,8)+"…"+s.slice(-6):s}
function fmt(v){if(!v)return "—";const d=new Date(v);return Number.isNaN(d.getTime())?String(v):d.toLocaleString()}
function pillClass(v){const x=String(v||"").toUpperCase();if(["SUCCEEDED","READY","QC_PASSED","RELEASE_APPROVED","PUBLISH_AUTHORIZED","PUBLISHED","CURRENT","VERIFIED","CLEANED_UP"].some(k=>x.includes(k)))return"good";if(["FAILED","REJECTED","BLOCKED","STALE","ERROR"].some(k=>x.includes(k)))return"bad";return"warn"}
function pill(v){return el("span","pill "+pillClass(v),v||"UNKNOWN")}
function toast(msg,bad=false){const n=byId("toast");n.textContent=msg;n.className="toast"+(bad?" bad":"");n.classList.remove("hidden");clearTimeout(toast.t);toast.t=setTimeout(()=>n.classList.add("hidden"),3200)}
async function api(url){const r=await fetch(url,{cache:"no-store",headers:{Accept:"application/json"}});const b=await r.json().catch(()=>({detail:"Invalid response"}));if(!r.ok)throw new Error(b.detail||("HTTP "+r.status));return b}
function pathJob(){const p="/animation/pipeline/";return location.pathname.startsWith(p)?location.pathname.slice(p.length):""}
function metric(label,value){const c=el("div","metric-card");c.append(el("div","metric-label",label),el("div","metric-value small",value??"—"));return c}
function evidenceCard(title,status,meta){const c=el("div","evidence-card"),h=el("div","evidence-head");h.append(el("div","evidence-title",title),pill(status));c.append(h);if(meta)c.append(el("div","evidence-meta",meta));return c}
function renderJobs(){
  const q=byId("jobFilter").value.trim().toLowerCase();const root=byId("jobList");clear(root);
  const rows=state.jobs.filter(x=>!q||JSON.stringify([x.id,x.episode_id,x.job_status,x.current_stage,x.review_status]).toLowerCase().includes(q));
  if(!rows.length){root.append(el("div","muted","没有匹配的 Job"));return}
  rows.forEach(x=>{const b=el("button","job-item"+(x.id===state.selected?" active":""));b.type="button";
    const h=el("div","job-item-head");h.append(el("div","job-item-title",x.episode_id||short(x.id)),pill(x.job_status));b.append(h,el("div","job-item-meta",(x.current_stage||"—")+" · Review "+(x.review_status||"—")+"\nPlans "+x.publish_plan_count+" · Exec "+x.publish_execution_count));
    b.addEventListener("click",()=>selectJob(x.id,true));root.append(b)
  })
}
function renderStages(job){
  const root=byId("stageTimeline");clear(root);const stages=job.stages||[];
  stages.forEach((s,i)=>{const c=el("div","pipeline-stage "+pillClass(s.stage_status));c.append(el("div","stage-index",String(i+1).padStart(2,"0")),el("div","stage-title",s.stage_key),pill(s.stage_status),el("div","stage-note","attempt "+(s.attempt_count??0)+"/"+(s.max_attempts??"—")+" · "+(s.finished_at?fmt(s.finished_at):"not finished")));if(s.last_error)c.append(el("div","evidence-meta",s.last_error));root.append(c)})
}
function renderPlans(rows){const root=byId("resourcePlans");clear(root);if(!rows.length){root.append(el("div","muted","尚无 Resource Plan"));return}rows.forEach(x=>root.append(evidenceCard(x.plan_kind+" v"+x.plan_version,x.plan_status,"planner "+(x.planner_version||"—")+" · "+short(x.content_sha256,22))))}
function renderArtifacts(rows){const root=byId("artifactList");clear(root);if(!rows.length){root.append(el("div","muted","尚无 Artifact"));return}rows.forEach(x=>root.append(evidenceCard(x.artifact_kind||x.artifact_type||"ARTIFACT",x.is_current===false?"SUPERSEDED":"CURRENT",(x.media_type||x.relative_path||x.storage_uri||"")+" · "+short(x.content_sha256||x.sha256,22))))}
function renderCompositions(rows){const root=byId("compositionList");clear(root);if(!rows.length){root.append(el("div","muted","尚无 Composition / Render"));return}rows.forEach(x=>root.append(evidenceCard(x.composition_kind||"ANIMATION COMPOSITION",x.composition_status||"CURRENT",(x.renderer||x.renderer_version||"")+" · "+short(x.content_sha256||x.composition_sha256||x.render_artifact_sha256,22))))}
function renderReview(d){const root=byId("reviewPanel");clear(root);if(!d.review){root.append(evidenceCard("Human Review","NOT READY",d.review_error||"Review package 尚未生成"));return}const r=d.review;root.append(evidenceCard(r.title||r.episode_id||"Episode",r.review_status||"UNKNOWN","QC "+(r.qc_passed===true?"PASSED":"—")+" · bundle "+short(r.episode_bundle_sha256,22)));if(r.release_review_package_sha256)root.append(evidenceCard("Release Review Package","CURRENT",short(r.release_review_package_sha256,26)))}
function renderPublishing(d){
  const p=byId("publishPlans");clear(p);if(!d.publish_plans.length)p.append(el("div","muted","尚无 Publish Plan"));d.publish_plans.forEach(x=>p.append(evidenceCard((x.platform||"—")+" · "+(x.target_key||"—"),x.plan_status||"UNKNOWN","plan "+short(x.plan_sha256,22)+" · dry-run "+short(x.dry_run_sha256,22))));
  const e=byId("publishExecutions");clear(e);if(!d.publish_executions.length)e.append(el("div","muted","尚无 Controlled Publisher Execution"));d.publish_executions.forEach(x=>e.append(evidenceCard((x.platform||"—")+" · "+(x.target_key||"—"),x.execution_status||"UNKNOWN","upload "+(x.upload_outcome||"—")+" "+(x.upload_write_count??0)+"/1 · publish "+(x.publish_outcome||"—")+" "+(x.publish_write_count??0)+"/1")));
  (d.bilibili_acceptances||[]).forEach(x=>e.append(evidenceCard("Bilibili Provider Read-Back",x.acceptance_status||"UNKNOWN","read-back "+Boolean(x.provider_read_back_verified)+" · private "+Boolean(x.private_visibility_verified)+" · cleanup "+Boolean(x.cleanup_verified))))
}
function renderHashes(d){const root=byId("hashGrid");clear(root);Object.entries(d.evidence||{}).forEach(([k,v])=>{if(!v)return;const c=el("div","hash-card");c.append(el("div","hash-label",k.replaceAll("_sha256","").replaceAll("_"," ")),el("div","hash-value",v));root.append(c)});if(!root.children.length)root.append(el("div","muted","尚无 hash evidence"))}
function renderDetail(d){
  state.detail=d;const j=d.job,s=j.shrimp_animation||{};byId("emptyState").classList.add("hidden");byId("jobDetail").classList.remove("hidden");
  byId("episodeTitle").textContent=s.episode_id||"Shrimp Animation Job";byId("jobMeta").textContent=j.id+" · "+(j.requested_by||"—")+" · updated "+fmt(j.updated_at);
  const st=byId("jobStatus");st.textContent=j.job_status||"UNKNOWN";st.className="pill "+pillClass(j.job_status);byId("currentStage").textContent=j.current_stage||"—";
  const m=byId("jobMetrics");clear(m);m.append(metric("Current Stage",j.current_stage),metric("Review",s.review_status||"—"),metric("Artifacts",(d.artifacts||[]).length),metric("Executions",(d.publish_executions||[]).length));
  const sc=byId("safetyChecks");clear(sc);[["External side effects",d.safety.external_side_effects==="DENY"],["Production execution",!d.safety.production_execution_enabled],["Provider publish flag",!d.safety.publish_enabled],["Console writes",!d.safety.console_write_actions]].forEach(([k,v])=>{const n=el("div","check-item");n.append(el("span","",k),pill(v?"SAFE":"BLOCKED"));sc.append(n)});
  byId("reviewLink").href="/animation-review/"+j.id;byId("publishingLink").href="/animation-publishing/"+j.id;
  renderStages(j);renderPlans(d.resource_plans||[]);renderArtifacts(d.artifacts||[]);renderCompositions(d.compositions||[]);renderReview(d);renderPublishing(d);renderHashes(d)
}
async function selectJob(id,push){state.selected=id;if(push)history.pushState({id},"","/animation/pipeline/"+id);renderJobs();try{renderDetail(await api("/v1/shrimp-animation/jobs/"+id+"/pipeline-console"))}catch(e){toast("Job detail 载入失败："+e.message,true)}}
async function load(){const data=await api("/v1/shrimp-animation/pipeline-console?limit=200");state.jobs=data.jobs||[];byId("jobCount").textContent=state.jobs.length+" JOBS";renderJobs();const deep=pathJob();const target=deep&&state.jobs.some(x=>x.id===deep)?deep:(state.selected&&state.jobs.some(x=>x.id===state.selected)?state.selected:state.jobs[0]?.id);if(target)await selectJob(target,false)}
byId("jobFilter").addEventListener("input",renderJobs);byId("refreshButton").addEventListener("click",async()=>{try{await load();toast("已重新整理")}catch(e){toast("刷新失败："+e.message,true)}});window.addEventListener("popstate",async()=>{const id=pathJob();if(id)await selectJob(id,false)});
(async()=>{try{await load()}catch(e){toast("Pipeline Console 载入失败："+e.message,true)}})();
