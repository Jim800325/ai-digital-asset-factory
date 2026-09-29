"use strict";

const state={
  candidates:[],
  selected:null,
  activeFile:null,
};

const byId=(id)=>document.getElementById(id);

function node(tag,cls,text){
  const el=document.createElement(tag);
  if(cls) el.className=cls;
  if(text!==undefined && text!==null) el.textContent=String(text);
  return el;
}

function clear(el){
  el.replaceChildren();
}

function shortHash(value){
  if(!value) return "—";
  return value.length>18 ? value.slice(0,10)+"…"+value.slice(-7) : value;
}

function fmtDate(value){
  if(!value) return "—";
  try{
    return new Intl.DateTimeFormat("zh-Hant",{
      year:"numeric",month:"2-digit",day:"2-digit",
      hour:"2-digit",minute:"2-digit",second:"2-digit",
      hour12:false,
    }).format(new Date(value));
  }catch{
    return String(value);
  }
}

function fmtMoney(value){
  const n=Number(value||0);
  return "$"+n.toFixed(4);
}

function statusClass(value){
  const s=String(value||"").toUpperCase();
  if(["PASS","PASSED","CURRENT","APPROVED","ARTIFACT_READY","GENERATED","WITHIN_BUDGET","READY_FOR_REVIEW","RELEASE_APPROVED","AUTHORIZED_FOR_DEPLOYMENT","LOW"].includes(s)) return "good";
  if(["FAILED","BLOCKED","REJECTED","RELEASE_REJECTED","DEPLOYMENT_REJECTED","HIGH","STALE","TAMPERED"].includes(s)) return "bad";
  if(["WAITING_LIVE_VALIDATION","PENDING_APPROVAL","PENDING_AUTHORIZATION","MEDIUM","NOT_EVALUATED","ORPHANED"].includes(s)) return "warn";
  return "info";
}

function addPill(parent,text,value){
  const p=node("span","pill "+statusClass(value),text);
  parent.appendChild(p);
  return p;
}

function showToast(message,error=false){
  const toast=byId("toast");
  toast.textContent=message;
  toast.className="toast"+(error?" bad":"");
  toast.classList.remove("hidden");
  window.clearTimeout(showToast._timer);
  showToast._timer=window.setTimeout(()=>toast.classList.add("hidden"),4200);
}

async function api(url,options={}){
  const response=await fetch(url,{
    cache:"no-store",
    headers:{
      "Accept":"application/json",
      ...(options.headers||{}),
    },
    ...options,
  });
  let payload=null;
  const type=response.headers.get("content-type")||"";
  if(type.includes("application/json")){
    payload=await response.json();
  }else{
    payload=await response.text();
  }
  if(!response.ok){
    const detail=payload && payload.detail ? payload.detail : payload;
    if(detail && typeof detail==="object" && detail.status==="DB_UNAVAILABLE"){
      const err=new Error(
        "DB_UNAVAILABLE · "+(detail.approval_mode||"READ_DEGRADED")
      );
      err.code="DB_UNAVAILABLE";
      err.payload=detail;
      throw err;
    }
    throw new Error(typeof detail==="string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

function summaryCard(label,value,kind){
  const card=node("div","summary-card");
  card.appendChild(node("div","summary-label",label));
  const val=node("div","summary-value",value);
  if(kind) val.classList.add(kind);
  card.appendChild(val);
  return card;
}

function kv(label,value,mono=false){
  const wrap=node("div","kv");
  wrap.appendChild(node("div","kv-label",label));
  const v=node("div","kv-value"+(mono?" code":""),value===null||value===undefined?"—":value);
  wrap.appendChild(v);
  return wrap;
}

function section(title){
  const card=node("div","section-card");
  if(title) card.appendChild(node("h3","",title));
  return card;
}

function copyRow(label,value){
  const wrap=kv(label,"");
  const holder=node("div","copy-row");
  holder.appendChild(node("div","hash",value||"—"));
  const btn=node("button","copy-button","複製");
  btn.type="button";
  btn.disabled=!value;
  btn.addEventListener("click",async()=>{
    try{
      await navigator.clipboard.writeText(value||"");
      showToast("已複製");
    }catch{
      showToast("瀏覽器不允許複製",true);
    }
  });
  holder.appendChild(btn);
  const valueBox=wrap.querySelector(".kv-value");
  valueBox.replaceChildren(holder);
  return wrap;
}

async function loadCandidates(){
  const list=byId("candidateList");
  clear(list);
  list.appendChild(node("div","loading","正在載入 Release Candidates…"));
  try{
    state.candidates=await api("/v1/review-workspace?limit=100");
    renderCandidates();
    const deepLink=location.pathname.startsWith("/review/")
      ? location.pathname.slice("/review/".length)
      : "";
    const target=deepLink || (state.selected && state.selected.release_candidate_id);
    if(target && state.candidates.some(x=>x.release_candidate_id===target)){
      await selectCandidate(target,false);
    }else if(state.candidates.length && !state.selected){
      await selectCandidate(state.candidates[0].release_candidate_id,false);
    }
  }catch(err){
    clear(list);
    if(err.code==="DB_UNAVAILABLE"){
      const box=node("div","notice warn");
      box.appendChild(node("strong","","DB_UNAVAILABLE"));
      box.appendChild(node(
        "div",
        "",
        "資料庫暫時不可用；Review Workspace 進入 READ_DEGRADED，Release Approval 保持 APPROVAL_FAIL_CLOSED。請稍後重新整理。"
      ));
      list.appendChild(box);
      byId("candidateCount").textContent="DB unavailable";
    }else{
      list.appendChild(node("div","loading","載入失敗："+err.message));
    }
  }
}

function renderCandidates(){
  const list=byId("candidateList");
  clear(list);
  byId("candidateCount").textContent=state.candidates.length+" 個候選";
  if(!state.candidates.length){
    list.appendChild(node("div","loading","目前沒有 Release Candidate。"));
    return;
  }
  for(const item of state.candidates){
    const btn=node("button","candidate");
    btn.type="button";
    btn.dataset.id=item.release_candidate_id;
    if(state.selected && state.selected.release_candidate_id===item.release_candidate_id){
      btn.classList.add("active");
    }

    btn.appendChild(node("div","candidate-title",item.opportunity_title||item.proposal_title||"未命名候選"));
    const meta=node("div","candidate-meta");
    addPill(meta,item.release_status,item.release_status);
    addPill(meta,"Integrity "+(item.integrity_status||"ORPHANED"),item.integrity_status||"ORPHANED");
    if(item.deployment_plan_status){
      addPill(meta,"DeployAuth "+item.deployment_plan_status,item.deployment_plan_status);
    }
    addPill(meta,"風險 "+(item.risk_level||"—"),item.risk_level);
    meta.appendChild(node("span","",String(item.artifact_count||0)+" files"));
    btn.appendChild(meta);
    btn.appendChild(node(
      "div",
      "candidate-hash",
      "pkg "+shortHash(item.package_sha256)+" · "+fmtDate(item.updated_at),
    ));
    btn.addEventListener("click",()=>selectCandidate(item.release_candidate_id,true));
    list.appendChild(btn);
  }
}

async function selectCandidate(id,push=true){
  byId("emptyState").classList.add("hidden");
  byId("detailContent").classList.remove("hidden");
  byId("candidateTitle").textContent="載入中…";
  if(push){
    history.pushState({candidateId:id},"","/review/"+id);
  }
  try{
    state.selected=await api("/v1/review-workspace/"+encodeURIComponent(id));
    state.activeFile=null;
    renderCandidates();
    renderDetail(state.selected);
  }catch(err){
    if(err.code==="DB_UNAVAILABLE"){
      showToast("DB_UNAVAILABLE：資料庫暫時不可用，Release Approval 已 fail-closed。",true);
      byId("candidateTitle").textContent="DB_UNAVAILABLE";
    }else{
      showToast("候選載入失敗："+err.message,true);
      byId("candidateTitle").textContent="載入失敗";
    }
  }
}

function renderDetail(d){
  byId("candidateKicker").textContent="Release Candidate · "+d.release_candidate_id;
  byId("candidateTitle").textContent=d.opportunity_title||d.proposal_title||"未命名候選";
  byId("candidateSubtitle").textContent=
    (d.asset_type||d.artifact_type||"ARTIFACT")+" · Proposal revision "+d.proposal_revision+
    " · "+fmtDate(d.candidate_updated_at);

  const badges=byId("statusBadges");
  clear(badges);
  addPill(badges,d.release_status,d.release_status);
  addPill(badges,"Integrity "+(d.integrity_status||"ORPHANED"),d.integrity_status||"ORPHANED");
  addPill(badges,"Live "+(d.live_validation_verified?"VERIFIED":"NOT VERIFIED"),d.live_validation_verified?"APPROVED":"WAITING_LIVE_VALIDATION");
  if(d.deployment_plan?.plan_status){
    addPill(badges,"DeployAuth "+d.deployment_plan.plan_status,d.deployment_plan.plan_status);
  }
  addPill(badges,"Executor DISABLED","APPROVED");

  const summary=byId("summaryCards");
  clear(summary);
  summary.append(
    summaryCard("Release Status",d.release_status||"—"),
    summaryCard("Integrity",d.integrity_status||"ORPHANED"),
    summaryCard("Deploy Auth",d.deployment_plan?.plan_status||"NOT PLANNED"),
    summaryCard("Static Risk",(d.risk_summary&&d.risk_summary.risk_level)||"—"),
    summaryCard("Tests",(d.test_report?.passed||0)+" / "+(d.test_report?.total||0)+" pass"),
    summaryCard("Artifacts",String((d.artifact_manifest||[]).length)),
    summaryCard("Dependencies",String((d.dependency_inventory||[]).length)),
  );

  renderOverview(d);
  renderFiles(d);
  renderDependencies(d);
  renderTests(d);
  renderRisks(d);
  renderHashes(d);
  renderDecision(d);
}

function renderOverview(d){
  const panel=byId("tab-overview");
  clear(panel);

  const boundary=section("Release Boundary");
  const grid=node("div","kv-grid");
  grid.append(
    kv("Release status",d.release_status),
    kv("Controlled Live LLM",d.live_validation_verified?"VERIFIED":"DEFERRED / NOT VERIFIED"),
    kv("Evidence integrity",d.integrity_status||"ORPHANED"),
    kv("Integrity audit ID",d.integrity_gate?.audit_id||"—",true),
    kv("Deployment",d.deployment_enabled?"ENABLED":"DISABLED"),
    kv("Deployment authorization",d.deployment_plan?.plan_status||"NOT PLANNED"),
    kv("Production executor","DISABLED"),
    kv("Sandbox request",d.request_status),
    kv("Gateway / budget",(d.gateway_mode||"—")+" / "+(d.budget_status||"—")),
  );
  boundary.appendChild(grid);

  const notice=node(
    "div",
    "notice "+(d.can_approve?"":"warn"),
    d.can_approve
      ? "此候選已同時滿足 Live Validation、Review Package 與 Evidence Integrity Gate；可以進入人工 Release Approval，批准仍不會啟用部署。"
      : "Release Approval 目前鎖定。阻擋原因："+((d.integrity_blocking_reasons||[]).join(", ")||"尚未滿足全部 Live / Review / Integrity 硬門檻")+"。",
  );
  boundary.appendChild(notice);
  panel.appendChild(boundary);

  const proposal=section("Proposal");
  const pgrid=node("div","kv-grid");
  pgrid.append(
    kv("Title",d.proposal_title),
    kv("Artifact type",d.artifact_type),
    kv("Build readiness",d.build_readiness),
    kv("Proposal status",d.proposal_status),
  );
  proposal.appendChild(pgrid);
  if(d.objective){
    const objective=node("div","notice");
    objective.style.marginTop="10px";
    objective.textContent=d.objective;
    proposal.appendChild(objective);
  }
  panel.appendChild(proposal);

  const runtime=section("OpenHands / Sandbox");
  const rgrid=node("div","kv-grid");
  rgrid.append(
    kv("Executor",d.executor_kind),
    kv("OpenHands CLI",d.cli_version),
    kv("Model",d.model_name),
    kv("Network policy",d.network_policy),
    kv("External side effects",d.external_side_effects),
    kv("Estimated model cost",fmtMoney(d.estimated_cost_usd)),
  );
  runtime.appendChild(rgrid);
  panel.appendChild(runtime);
}

function renderFiles(d){
  const panel=byId("tab-files");
  clear(panel);
  const diffs=Array.isArray(d.artifact_diff)?d.artifact_diff:[];
  if(!diffs.length){
    panel.appendChild(node("div","notice warn","沒有 Artifact diff。Review Package 可能尚未生成。"));
    return;
  }

  const layout=node("div","file-layout");
  const list=node("div","file-list");
  const viewer=node("div","diff-wrap");
  const meta=node("div","section-card");
  const pre=node("pre","diff-pre","選擇左側檔案查看 diff。");
  viewer.append(meta,pre);

  function selectFile(item,button){
    state.activeFile=item.relative_path;
    list.querySelectorAll(".file-button").forEach(x=>x.classList.remove("active"));
    button.classList.add("active");
    clear(meta);
    meta.appendChild(node("h3","",item.relative_path));
    const grid=node("div","kv-grid");
    grid.append(
      kv("Status",item.status),
      kv("Before size",item.before_size===null?"—":item.before_size+" bytes"),
      kv("After size",item.after_size===null?"—":item.after_size+" bytes"),
      kv("Diff truncated",item.diff_truncated?"YES":"NO"),
    );
    meta.appendChild(grid);
    const patch=item.text_diff;
    if(patch){
      pre.textContent=patch;
    }else if(item.status==="UNCHANGED"){
      pre.textContent="此檔案與 baseline 相同。\nSHA-256: "+(item.after_sha256||"—");
    }else{
      pre.textContent=
        "此檔案沒有可顯示的 UTF-8 text diff（可能是二進位或超過掃描上限）。\n\n"+
        "Before SHA-256: "+(item.before_sha256||"—")+"\n"+
        "After SHA-256:  "+(item.after_sha256||"—");
    }
  }

  diffs.forEach((item,index)=>{
    const btn=node("button","file-button");
    btn.type="button";
    btn.appendChild(node("div","file-name",item.relative_path));
    btn.appendChild(node("div","file-state",item.status+" · "+shortHash(item.after_sha256||item.before_sha256)));
    btn.addEventListener("click",()=>selectFile(item,btn));
    list.appendChild(btn);
    if(index===0) window.setTimeout(()=>selectFile(item,btn),0);
  });

  layout.append(list,viewer);
  panel.appendChild(layout);
}

function renderDependencies(d){
  const panel=byId("tab-dependencies");
  clear(panel);
  const deps=Array.isArray(d.dependency_inventory)?d.dependency_inventory:[];
  const card=section("Dependency Inventory");
  if(!deps.length){
    card.appendChild(node("div","notice","未在捕獲 Artifact 中找到支援的依賴宣告檔。"));
  }else{
    const wrap=node("div","table-wrap");
    const table=node("table");
    const head=node("thead");
    const hr=node("tr");
    ["Ecosystem","Name","Version / Spec","Scope","Source"].forEach(x=>hr.appendChild(node("th","",x)));
    head.appendChild(hr);
    const body=node("tbody");
    deps.forEach(dep=>{
      const tr=node("tr");
      [
        dep.ecosystem||"—",
        dep.name||"—",
        dep.version_spec||"—",
        dep.scope||"—",
        dep.source_file||"—",
      ].forEach(x=>tr.appendChild(node("td","",x)));
      body.appendChild(tr);
    });
    table.append(head,body);
    wrap.appendChild(table);
    card.appendChild(wrap);
  }
  panel.appendChild(card);

  const sbom=section("SBOM");
  const sbomMeta=node("div","kv-grid");
  sbomMeta.append(
    kv("Format",d.sbom?.bomFormat||"—"),
    kv("Spec version",d.sbom?.specVersion||"—"),
    kv("Components",String(d.sbom?.components?.length||0)),
    kv("Network lookup","NONE"),
  );
  sbom.appendChild(sbomMeta);
  const pre=node("pre","log-pre",JSON.stringify(d.sbom||{},null,2));
  pre.style.marginTop="10px";
  sbom.appendChild(pre);
  panel.appendChild(sbom);
}

function renderTests(d){
  const panel=byId("tab-tests");
  clear(panel);
  const report=d.test_report||{};
  const summary=section("Independent Test Report");
  const grid=node("div","kv-grid");
  grid.append(
    kv("Total",report.total||0),
    kv("Passed",report.passed||0),
    kv("Failed",report.failed||0),
    kv("All passed",report.all_passed?"YES":"NO"),
  );
  summary.appendChild(grid);
  panel.appendChild(summary);

  for(const item of report.results||[]){
    const card=section(item.passed?"PASS":"FAIL");
    const meta=node("div","kv-grid");
    meta.append(
      kv("Command",item.test_command,true),
      kv("Exit code",item.exit_code),
    );
    card.appendChild(meta);
    if(item.stdout){
      card.appendChild(node("h3","","STDOUT"));
      card.appendChild(node("pre","log-pre",item.stdout));
    }
    if(item.stderr){
      card.appendChild(node("h3","","STDERR"));
      card.appendChild(node("pre","log-pre",item.stderr));
    }
    panel.appendChild(card);
  }
}

function renderRisks(d){
  const panel=byId("tab-risks");
  clear(panel);
  const risk=d.risk_summary||{};
  const summary=section("Static Risk Summary");
  const grid=node("div","kv-grid");
  grid.append(
    kv("Risk level",risk.risk_level||"—"),
    kv("Findings",risk.finding_count||0),
    kv("Scanned text files",risk.scanned_text_files||0),
    kv("Skipped binary files",risk.skipped_binary_files||0),
  );
  summary.appendChild(grid);
  const n=node("div","notice warn",risk.note||"Static heuristic review only.");
  n.style.marginTop="10px";
  summary.appendChild(n);
  panel.appendChild(summary);

  const findings=Array.isArray(risk.findings)?risk.findings:[];
  const card=section("Findings");
  if(!findings.length){
    card.appendChild(node("div","notice","目前沒有靜態規則命中。這不代表程式碼已獲安全認證。"));
  }else{
    findings.forEach(item=>{
      const row=node("div","risk-item");
      addPill(row,item.severity,item.severity);
      row.appendChild(node("div","code",item.rule));
      row.appendChild(node("div","",item.relative_path+(item.detail?" · "+item.detail:"")));
      card.appendChild(row);
    });
  }
  panel.appendChild(card);
}

function renderHashes(d){
  const panel=byId("tab-hashes");
  clear(panel);
  const card=section("Integrity Hashes");
  const grid=node("div","kv-grid");
  grid.append(
    copyRow("Review package SHA-256",d.package_sha256),
    copyRow("Source tree SHA-256",d.source_tree_sha256),
    copyRow("Artifact manifest SHA-256",d.artifact_manifest_sha256),
    copyRow("Source fingerprint",d.source_fingerprint),
    copyRow("Audit evidence SHA-256",d.integrity_gate?.audit_evidence_sha256),
    copyRow("Audit chain SHA-256",d.integrity_gate?.audit_chain_sha256),
    copyRow("Manifest root SHA-256",d.integrity_gate?.manifest_root_sha256),
    copyRow("Chain head SHA-256",d.integrity_gate?.chain_head_sha256),
  );
  card.appendChild(grid);
  panel.appendChild(card);

  const provenance=section("Provenance");
  const pgrid=node("div","kv-grid");
  pgrid.append(
    kv("Review package ID",d.review_package_id,true),
    kv("Baseline package ID",d.baseline_package_id||"—",true),
    kv("Generator",d.generator_version),
    kv("Generated",fmtDate(d.review_generated_at)),
    kv("Content snapshot complete",d.content_snapshot_complete?"YES":"NO"),
    kv("Proposal revision",d.proposal_revision),
    kv("Integrity status",d.integrity_status||"ORPHANED"),
    kv("Integrity audit ID",d.integrity_gate?.audit_id||"—",true),
    kv("Vercel deployment",d.integrity_gate?.vercel_deployment_id||"—",true),
    kv("Deployment provenance",d.integrity_gate?.deployment_source_commit||"—",true),
    kv("Blocking reasons",(d.integrity_blocking_reasons||[]).join(", ")||"none"),
  );
  provenance.appendChild(pgrid);
  panel.appendChild(provenance);
}

function renderDeploymentAuthorization(d,panel){
  const card=section("Deployment Authorization");
  const plan=d.deployment_plan||null;
  const grid=node("div","kv-grid");
  grid.append(
    kv("Authorization key configured",d.deployment_authorization_gate_configured?"YES":"NO"),
    kv("Plan status",plan?.plan_status||"NOT PLANNED"),
    kv("Target provider",plan?.target_provider||"VERCEL"),
    kv("Target environment",plan?.target_environment||"production"),
    kv("Production executor","DISABLED"),
    kv("Execution enabled",plan?.execution_enabled?"YES":"NO"),
  );
  card.appendChild(grid);

  const boundary=node(
    "div",
    "notice warn",
    "Deployment Authorization 是第二道人工作業閘門。即使狀態變成 AUTHORIZED_FOR_DEPLOYMENT，也不會執行 Vercel Production deploy；executor 目前固定 DISABLED。"
  );
  boundary.style.marginTop="10px";
  card.appendChild(boundary);

  if(plan){
    const hashes=node("div","kv-grid");
    hashes.style.marginTop="10px";
    hashes.append(
      copyRow("Deployment Plan SHA-256",plan.plan_sha256),
      copyRow("Acceptance provenance tree",plan.acceptance_provenance_tree_sha256),
      kv("Live Acceptance Audit",plan.live_acceptance_audit_id||"—",true),
      kv("Target project",plan.target_project_id||"—",true),
      kv("Target team",plan.target_team_id||"—",true),
    );
    card.appendChild(hashes);

    if((plan.blocks||[]).length){
      const blocked=section("Deployment Authorization Block History");
      const timeline=node("div","timeline");
      plan.blocks.forEach(item=>{
        const box=node("div","timeline-item");
        const head=node("div","timeline-head");
        addPill(head,"BLOCKED",item.integrity_status||"BLOCKED");
        head.appendChild(node("strong","",item.actor||"unknown"));
        head.appendChild(node("span","muted",fmtDate(item.blocked_at)));
        box.appendChild(head);
        box.appendChild(node("div","",item.reason||"Authorization blocked."));
        timeline.appendChild(box);
      });
      blocked.appendChild(timeline);
      card.appendChild(blocked);
    }

    if((plan.decisions||[]).length){
      const history=section("Deployment Authorization History");
      const timeline=node("div","timeline");
      plan.decisions.forEach(item=>{
        const box=node("div","timeline-item");
        const head=node("div","timeline-head");
        addPill(
          head,
          item.decision,
          item.decision==="AUTHORIZE"?"AUTHORIZED_FOR_DEPLOYMENT":"DEPLOYMENT_REJECTED"
        );
        head.appendChild(node("strong","",item.actor||"unknown"));
        head.appendChild(node("span","muted",fmtDate(item.decided_at)));
        box.appendChild(head);
        box.appendChild(node("div","",item.reason||"—"));
        timeline.appendChild(box);
      });
      history.appendChild(timeline);
      card.appendChild(history);
    }

    if(plan.plan_status==="PENDING_AUTHORIZATION"){
      const form=node("div","decision-box");
      form.style.marginTop="12px";

      const keyField=node("div","field");
      keyField.appendChild(node("label","","X-Deployment-Key"));
      const key=node("input");
      key.type="password";
      key.autocomplete="off";
      key.spellcheck=false;
      key.placeholder="第二把人工授權金鑰；不會儲存在瀏覽器";
      keyField.appendChild(key);

      const actorField=node("div","field");
      actorField.appendChild(node("label","","Deployment reviewer"));
      const actor=node("input");
      actor.value="human-deployment-ui";
      actor.maxLength=200;
      actorField.appendChild(actor);

      const reasonField=node("div","field");
      reasonField.appendChild(node("label","","Authorization reason"));
      const reason=node("textarea");
      reason.maxLength=4000;
      reason.placeholder="記錄 AUTHORIZE / REJECT 的理由";
      reasonField.appendChild(reason);

      const actions=node("div","decision-actions");
      const authorize=node("button","button approve","AUTHORIZE FOR DEPLOYMENT");
      authorize.type="button";
      authorize.disabled=!d.can_authorize_deployment;
      const reject=node("button","button danger","REJECT DEPLOYMENT");
      reject.type="button";
      reject.disabled=!d.deployment_authorization_gate_configured;
      actions.append(authorize,reject);

      async function submit(decision){
        if(reason.value.trim().length<3){
          showToast("請填寫至少 3 個字元的授權理由",true);
          return;
        }
        if(!key.value){
          showToast("請輸入 X-Deployment-Key",true);
          return;
        }
        const warning=decision==="AUTHORIZE"
          ? "確認授權此 Deployment Plan？\n\n這只會寫入 AUTHORIZED_FOR_DEPLOYMENT；Production executor 仍為 DISABLED，不會部署。"
          : "確認拒絕此 Deployment Plan？";
        if(!window.confirm(warning)) return;
        authorize.disabled=true;
        reject.disabled=true;
        try{
          await api("/v1/deployment-plans/"+encodeURIComponent(plan.id)+"/decision",{
            method:"POST",
            headers:{
              "Content-Type":"application/json",
              "X-Deployment-Key":key.value,
            },
            body:JSON.stringify({
              decision,
              reason:reason.value.trim(),
              actor:actor.value.trim()||"human-deployment-ui",
              plan_sha256:plan.plan_sha256,
            }),
          });
          key.value="";
          reason.value="";
          showToast(
            decision==="AUTHORIZE"
              ? "Deployment Plan 已授權；Production executor 仍為 DISABLED。"
              : "Deployment Plan 已拒絕。"
          );
          await loadCandidates();
          await selectCandidate(d.release_candidate_id,false);
        }catch(err){
          key.value="";
          showToast("Deployment Authorization 被拒絕："+err.message,true);
          await selectCandidate(d.release_candidate_id,false);
        }
      }

      authorize.addEventListener("click",()=>submit("AUTHORIZE"));
      reject.addEventListener("click",()=>submit("REJECT"));
      form.append(keyField,actorField,reasonField,actions);
      card.appendChild(form);
    }
  }else if(d.can_create_deployment_plan){
    const form=node("div","decision-box");
    form.style.marginTop="12px";

    const projectField=node("div","field");
    projectField.appendChild(node("label","","Target Vercel Project ID"));
    const project=node("input");
    project.placeholder="prj_... 或可識別的 project id";
    project.maxLength=160;
    projectField.appendChild(project);

    const teamField=node("div","field");
    teamField.appendChild(node("label","","Target Team ID（可選）"));
    const team=node("input");
    team.placeholder="team_...";
    team.maxLength=160;
    teamField.appendChild(team);

    const keyField=node("div","field");
    keyField.appendChild(node("label","","X-Deployment-Key"));
    const key=node("input");
    key.type="password";
    key.autocomplete="off";
    key.spellcheck=false;
    key.placeholder="第二把人工授權金鑰";
    keyField.appendChild(key);

    const actorField=node("div","field");
    actorField.appendChild(node("label","","Planner"));
    const actor=node("input");
    actor.value="human-deployment-ui";
    actor.maxLength=200;
    actorField.appendChild(actor);

    const create=node("button","button","生成不可變 Deployment Plan");
    create.type="button";
    create.disabled=!d.deployment_authorization_gate_configured;
    create.addEventListener("click",async()=>{
      if(project.value.trim().length<3){
        showToast("請填寫 Target Vercel Project ID",true);
        return;
      }
      if(!key.value){
        showToast("請輸入 X-Deployment-Key",true);
        return;
      }
      if(!window.confirm(
        "確認生成不可變 Deployment Plan？\n\n此動作只固化目標與 provenance，不會執行部署。"
      )) return;
      create.disabled=true;
      try{
        await api("/v1/release-candidates/"+encodeURIComponent(d.release_candidate_id)+"/deployment-plan",{
          method:"POST",
          headers:{
            "Content-Type":"application/json",
            "X-Deployment-Key":key.value,
          },
          body:JSON.stringify({
            target_project_id:project.value.trim(),
            target_team_id:team.value.trim()||null,
            actor:actor.value.trim()||"human-deployment-ui",
          }),
        });
        key.value="";
        showToast("不可變 Deployment Plan 已生成；等待第二道人工作業授權。");
        await loadCandidates();
        await selectCandidate(d.release_candidate_id,false);
      }catch(err){
        key.value="";
        create.disabled=false;
        showToast("Deployment Plan 建立失敗："+err.message,true);
      }
    });

    form.append(projectField,teamField,keyField,actorField,create);
    card.appendChild(form);
  }else if(d.release_status==="RELEASE_APPROVED"){
    const why=node(
      "div",
      "notice warn",
      d.archived_at
        ? "此 Release Candidate 已歸檔，不能建立 Deployment Plan。"
        : "目前不符合 Deployment Plan 建立條件。需要完整 Review Package、VERIFIED provenance，且 deployment/execution 必須保持 DISABLED。"
    );
    why.style.marginTop="10px";
    card.appendChild(why);
  }

  if(!d.deployment_authorization_gate_configured){
    const n=node(
      "div",
      "notice warn",
      "伺服器尚未配置 HUMAN_DEPLOYMENT_KEY，因此 Deployment Authorization 保持關閉。"
    );
    n.style.marginTop="10px";
    card.appendChild(n);
  }

  panel.appendChild(card);
}

function renderDecision(d){
  const panel=byId("tab-decision");
  clear(panel);

  const status=section("Decision Gate");
  const grid=node("div","kv-grid");
  grid.append(
    kv("Release status",d.release_status),
    kv("Live validation",d.live_validation_verified?"VERIFIED":"NOT VERIFIED"),
    kv("Integrity gate",d.integrity_status||"ORPHANED"),
    kv("Integrity audit",d.integrity_gate?.audit_id||"—",true),
    kv("Release key configured",d.release_gate_configured?"YES":"NO"),
    kv("Deployment",d.deployment_enabled?"ENABLED":"DISABLED"),
  );
  status.appendChild(grid);

  if(!d.can_approve){
    const why=node(
      "div",
      "notice warn",
      (d.integrity_gate_allowed===false)
        ? "批准按鈕已鎖定：Evidence Integrity Gate 未通過。"+((d.integrity_blocking_reasons||[]).length?" 原因："+d.integrity_blocking_reasons.join(", ")+"。":"")
        : d.release_status==="WAITING_LIVE_VALIDATION"
          ? "批准按鈕已鎖定：Controlled Live LLM Acceptance 尚未通過。可以繼續審查或人工 Reject，但不能 Approve。"
          : "此候選目前不符合 Release Approval 的硬門檻。",
    );
    why.style.marginTop="10px";
    status.appendChild(why);
  }
  panel.appendChild(status);

  if((d.integrity_block_events||[]).length){
    const blocked=section("Integrity Gate Block History");
    const timeline=node("div","timeline");
    d.integrity_block_events.forEach(item=>{
      const box=node("div","timeline-item");
      const head=node("div","timeline-head");
      addPill(head,"BLOCKED",item.integrity_status||"BLOCKED");
      addPill(head,item.integrity_status||"ORPHANED",item.integrity_status||"ORPHANED");
      head.appendChild(node("strong","",item.actor||"unknown"));
      head.appendChild(node("span","muted",fmtDate(item.blocked_at)));
      box.appendChild(head);
      box.appendChild(node("div","",item.reason||"Evidence Integrity Gate blocked approval."));
      if(item.live_acceptance_audit_id){
        box.appendChild(node("div","candidate-hash","audit "+item.live_acceptance_audit_id+" · chain "+shortHash(item.audit_chain_sha256)));
      }
      timeline.appendChild(box);
    });
    blocked.appendChild(timeline);
    panel.appendChild(blocked);
  }

  if((d.decisions||[]).length){
    const history=section("Decision History");
    const timeline=node("div","timeline");
    d.decisions.forEach(item=>{
      const box=node("div","timeline-item");
      const head=node("div","timeline-head");
      addPill(head,item.decision,item.decision==="APPROVE"?"APPROVED":"REJECTED");
      head.appendChild(node("strong","",item.actor));
      head.appendChild(node("span","muted",fmtDate(item.decided_at)));
      box.appendChild(head);
      box.appendChild(node("div","",item.reason));
      if(item.review_package_sha256){
        box.appendChild(node(
          "div",
          "candidate-hash",
          "review package "+shortHash(item.review_package_sha256),
        ));
      }
      timeline.appendChild(box);
    });
    history.appendChild(timeline);
    panel.appendChild(history);
  }

  renderDeploymentAuthorization(d,panel);

  const formCard=section("Human Decision");
  const form=node("div","decision-box");

  const keyField=node("div","field");
  keyField.appendChild(node("label","","X-Release-Key"));
  const key=node("input");
  key.type="password";
  key.autocomplete="off";
  key.spellcheck=false;
  key.placeholder="只用於本次請求，不會儲存在瀏覽器";
  keyField.appendChild(key);

  const actorField=node("div","field");
  actorField.appendChild(node("label","","Reviewer"));
  const actor=node("input");
  actor.value="human-review-ui";
  actor.maxLength=200;
  actorField.appendChild(actor);

  const reasonField=node("div","field");
  reasonField.appendChild(node("label","","Decision reason"));
  const reason=node("textarea");
  reason.maxLength=4000;
  reason.placeholder="記錄批准或拒絕的具體理由（至少 3 個字元）";
  reasonField.appendChild(reason);

  const actions=node("div","decision-actions");
  const approve=node("button","button approve","批准 Release");
  approve.type="button";
  approve.disabled=!d.can_approve || !d.release_gate_configured;
  const reject=node("button","button danger","拒絕 Release");
  reject.type="button";
  reject.disabled=!d.can_reject || !d.release_gate_configured;

  const isControlledFixture=(
    d.release_candidate_id==="00000000-0000-0000-0000-000000001709"
    && d.source_fingerprint==="test-only-release-gate-fixture-v1"
    && d.integrity_gate_allowed===false
  );
  const probeApprove=node(
    "button",
    "button",
    "TEST ONLY · 嘗試後端 APPROVE"
  );
  probeApprove.type="button";
  probeApprove.disabled=!isControlledFixture || !d.release_gate_configured;

  actions.append(approve,reject);
  if(isControlledFixture) actions.appendChild(probeApprove);

  async function submitDecision(decision,controlledProbe=false){
    if(reason.value.trim().length<3){
      showToast("請填寫至少 3 個字元的決策理由",true);
      return;
    }
    if(!key.value){
      showToast("請輸入 X-Release-Key",true);
      return;
    }
    const label=decision==="APPROVE"?"批准":"拒絕";
    const confirmText=controlledProbe
      ? "確認執行 TEST_ONLY 後端 APPROVE 探針？\n\n預期結果：後端 Evidence Integrity Gate 必須拒絕，寫入 release_gate_blocks，不得產生 APPROVE decision，也不得啟用部署。"
      : "確認"+label+"這個 Release Candidate？\n\n此操作會留下不可變決策紀錄，但不會部署。";
    if(!window.confirm(confirmText)){
      return;
    }
    approve.disabled=true;
    reject.disabled=true;
    probeApprove.disabled=true;
    try{
      await api("/v1/release-candidates/"+encodeURIComponent(d.release_candidate_id)+"/decision",{
        method:"POST",
        headers:{
          "Content-Type":"application/json",
          "X-Release-Key":key.value,
        },
        body:JSON.stringify({
          decision,
          reason:reason.value.trim(),
          actor:actor.value.trim()||"human-review-ui",
          review_package_sha256:d.package_sha256||null,
        }),
      });
      key.value="";
      reason.value="";
      showToast("Release 決策已記錄；部署仍保持 DISABLED。");
      await loadCandidates();
      await selectCandidate(d.release_candidate_id,false);
    }catch(err){
      key.value="";
      if(err.code==="DB_UNAVAILABLE"){
        showToast(
          "DB_UNAVAILABLE：決策未自動重試；Approval 保持 fail-closed。重新載入狀態後再人工確認。",
          true,
        );
      }else if(controlledProbe){
        showToast("TEST ONLY：後端已拒絕 APPROVE；正在重新讀取持久化阻擋紀錄。",true);
      }else{
        showToast("決策被拒絕："+err.message,true);
      }
      if(controlledProbe){
        await loadCandidates();
        await selectCandidate(d.release_candidate_id,false);
      }else{
        renderDecision(d);
      }
    }
  }

  approve.addEventListener("click",()=>submitDecision("APPROVE"));
  reject.addEventListener("click",()=>submitDecision("REJECT"));
  probeApprove.addEventListener("click",()=>{
    if(reason.value.trim().length<3){
      reason.value="Controlled TEST_ONLY fail-closed persistence acceptance";
    }
    actor.value="controlled-release-gate-test-ui";
    submitDecision("APPROVE",true);
  });

  form.append(keyField,actorField,reasonField,actions);
  formCard.appendChild(form);

  if(isControlledFixture){
    const testNotice=node(
      "div",
      "notice warn",
      "TEST_ONLY 探針只會對固定 Fixture 呼叫原本的 Release Decision API；不繞過 X-Release-Key，也不繞過後端 Integrity Gate。預期必須被拒絕。"
    );
    testNotice.style.marginTop="10px";
    formCard.appendChild(testNotice);
  }

  if(!d.release_gate_configured){
    const n=node("div","notice warn","伺服器尚未配置 HUMAN_RELEASE_KEY，因此 UI 決策功能保持關閉。");
    n.style.marginTop="10px";
    formCard.appendChild(n);
  }
  panel.appendChild(formCard);
}

function activateTab(name){
  document.querySelectorAll(".tab").forEach(btn=>{
    btn.classList.toggle("active",btn.dataset.tab===name);
  });
  document.querySelectorAll(".tab-panel").forEach(panel=>{
    panel.classList.toggle("active",panel.id==="tab-"+name);
  });
}

document.querySelectorAll(".tab").forEach(btn=>{
  btn.addEventListener("click",()=>activateTab(btn.dataset.tab));
});

byId("refreshButton").addEventListener("click",async()=>{
  const id=state.selected?.release_candidate_id;
  await loadCandidates();
  if(id) await selectCandidate(id,false);
  showToast("已重新整理");
});

window.addEventListener("popstate",async()=>{
  const id=location.pathname.startsWith("/review/")
    ? location.pathname.slice("/review/".length)
    : "";
  if(id){
    await selectCandidate(id,false);
  }else{
    state.selected=null;
    renderCandidates();
    byId("detailContent").classList.add("hidden");
    byId("emptyState").classList.remove("hidden");
  }
});

loadCandidates();
