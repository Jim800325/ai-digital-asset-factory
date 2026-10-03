"use strict";

const state={episodes:[],selected:null};
const byId=(id)=>document.getElementById(id);

function node(tag,cls,text){
  const el=document.createElement(tag);
  if(cls) el.className=cls;
  if(text!==undefined && text!==null) el.textContent=String(text);
  return el;
}
function clear(el){el.replaceChildren();}
function shortHash(value){
  if(!value) return "—";
  return value.length>20 ? value.slice(0,11)+"…"+value.slice(-8) : value;
}
function fmtDate(value){
  if(!value) return "—";
  try{
    return new Intl.DateTimeFormat("zh-Hant",{
      year:"numeric",month:"2-digit",day:"2-digit",
      hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false,
    }).format(new Date(value));
  }catch{return String(value);}
}
function fmtDuration(ms){
  const total=Math.max(0,Math.round(Number(ms||0)/1000));
  const m=Math.floor(total/60);
  const s=total%60;
  return m+":"+String(s).padStart(2,"0");
}
function statusClass(value){
  const s=String(value||"").toUpperCase();
  if(["READY_FOR_HUMAN_REVIEW","RELEASE_APPROVED","QC_PASSED","PASSED","CURRENT","APPROVE"].includes(s)) return "good";
  if(["RELEASE_REJECTED","FAILED","STALE","REJECT"].includes(s)) return "bad";
  if(["NOT_READY","PENDING"].includes(s)) return "warn";
  return "info";
}
function pill(text,value){
  return node("span","pill "+statusClass(value),text);
}
function showToast(message,error=false){
  const toast=byId("toast");
  toast.textContent=message;
  toast.className="toast"+(error?" bad":"");
  toast.classList.remove("hidden");
  window.clearTimeout(showToast._timer);
  showToast._timer=window.setTimeout(()=>toast.classList.add("hidden"),4500);
}
async function api(url,options={}){
  const response=await fetch(url,{
    cache:"no-store",
    headers:{"Accept":"application/json",...(options.headers||{})},
    ...options,
  });
  let payload=null;
  const type=response.headers.get("content-type")||"";
  if(type.includes("application/json")) payload=await response.json();
  else payload=await response.text();
  if(!response.ok){
    const detail=payload&&payload.detail?payload.detail:payload;
    throw new Error(typeof detail==="string"?detail:JSON.stringify(detail));
  }
  return payload;
}
function section(title){
  const card=node("div","section-card");
  if(title) card.appendChild(node("h3","",title));
  return card;
}
function kv(label,value,mono=false){
  const wrap=node("div","kv");
  wrap.appendChild(node("div","kv-label",label));
  wrap.appendChild(node("div","kv-value"+(mono?" code":""),value??"—"));
  return wrap;
}
function summaryCard(label,value){
  const card=node("div","summary-card");
  card.appendChild(node("div","summary-label",label));
  card.appendChild(node("div","summary-value",value??"—"));
  return card;
}
function copyHash(label,value){
  const wrap=kv(label,"");
  const row=node("div","copy-row");
  row.appendChild(node("div","hash",value||"—"));
  const button=node("button","copy-button","复制");
  button.type="button";
  button.disabled=!value;
  button.addEventListener("click",async()=>{
    try{
      await navigator.clipboard.writeText(value||"");
      showToast("已复制");
    }catch{
      showToast("浏览器不允许复制",true);
    }
  });
  row.appendChild(button);
  wrap.querySelector(".kv-value").replaceChildren(row);
  return wrap;
}

async function loadEpisodes(){
  const list=byId("episodeList");
  clear(list);
  list.appendChild(node("div","loading","正在载入 Episodes…"));
  try{
    state.episodes=await api("/v1/shrimp-animation/review-workspace?limit=100");
    renderEpisodes();
    const prefix="/animation-review/";
    const deep=location.pathname.startsWith(prefix)
      ? location.pathname.slice(prefix.length)
      : "";
    const target=deep||(state.selected&&state.selected.job_id);
    if(target&&state.episodes.some(x=>x.job_id===target)){
      await selectEpisode(target,false);
    }else if(state.episodes.length&&!state.selected){
      await selectEpisode(state.episodes[0].job_id,false);
    }
  }catch(err){
    clear(list);
    list.appendChild(node("div","loading","载入失败："+err.message));
  }
}

function renderEpisodes(){
  const list=byId("episodeList");
  clear(list);
  byId("episodeCount").textContent=state.episodes.length+" 个 Episode";
  if(!state.episodes.length){
    list.appendChild(node("div","loading","目前没有可审查 Episode。"));
    return;
  }
  for(const item of state.episodes){
    const button=node("button","candidate");
    button.type="button";
    if(state.selected&&state.selected.job_id===item.job_id) button.classList.add("active");
    button.appendChild(node("div","candidate-title",item.title||item.episode_id));
    const meta=node("div","candidate-meta");
    meta.append(
      pill(item.review_status,item.review_status),
      pill(item.job_status,item.job_status),
      node("span","",item.duration_ms?fmtDuration(item.duration_ms):"—")
    );
    button.appendChild(meta);
    button.appendChild(node(
      "div","candidate-hash",
      "bundle "+shortHash(item.episode_bundle_sha256)+" · "+fmtDate(item.updated_at)
    ));
    button.addEventListener("click",()=>selectEpisode(item.job_id,true));
    list.appendChild(button);
  }
}

async function selectEpisode(id,push=true){
  byId("emptyState").classList.add("hidden");
  byId("detailContent").classList.remove("hidden");
  byId("episodeTitle").textContent="载入中…";
  if(push) history.pushState({jobId:id},"","/animation-review/"+id);
  try{
    state.selected=await api(
      "/v1/shrimp-animation/review-workspace/"+encodeURIComponent(id)
    );
    renderEpisodes();
    renderDetail(state.selected);
  }catch(err){
    byId("episodeTitle").textContent="载入失败";
    showToast("Episode 载入失败："+err.message,true);
  }
}

function renderDetail(d){
  const e=d.episode||{};
  byId("episodeKicker").textContent="Episode · "+d.episode_id+" · "+d.job_id;
  byId("episodeTitle").textContent=e.title||d.episode_id;
  byId("episodeSubtitle").textContent=
    (e.language||"—")+" · "+fmtDuration(e.duration_ms)+" · "+
    (e.width||"—")+"x"+(e.height||"—")+" · "+(e.fps||"—")+" FPS";

  const badges=byId("statusBadges");
  clear(badges);
  badges.append(
    pill(d.review_status,d.review_status),
    pill("QC "+(d.qc?.passed?"PASSED":"NOT PASSED"),d.qc?.passed?"PASSED":"FAILED"),
    pill(d.integrity_gate?.allowed?"Integrity VERIFIED":"Integrity BLOCKED",
      d.integrity_gate?.allowed?"PASSED":"FAILED"),
    pill("Publish DISABLED","PASSED")
  );

  const summary=byId("summaryCards");
  clear(summary);
  summary.append(
    summaryCard("Review",d.review_status),
    summaryCard("QC failures",String(d.qc?.hard_failure_count??"—")),
    summaryCard("Bundle",shortHash(d.episode_bundle_sha256)),
    summaryCard("Review Package",shortHash(d.release_review_package_sha256)),
    summaryCard("Artifacts",String(d.bundle?.artifact_manifest?.length||0))
  );

  renderPlayer(d);
  renderQc(d);
  renderProvenance(d);
  renderBundle(d);
  renderTranscript(d);
  renderDecision(d);
}

function renderPlayer(d){
  const panel=byId("tab-player");
  clear(panel);
  const card=section("Episode Player");
  const shell=node("div","video-shell");
  const video=document.createElement("video");
  video.controls=true;
  video.preload="metadata";
  video.src=d.episode_player_url;
  video.setAttribute("playsinline","");
  shell.appendChild(video);

  const meta=node("div","player-meta");
  meta.append(
    pill(d.review_status,d.review_status),
    node("span","muted","播放器每次读取都会重新验证 MP4 SHA-256"),
    node("span","hash",shortHash(d.media?.render_artifact_sha256))
  );
  shell.appendChild(meta);
  card.appendChild(shell);

  const integrity=section("Review Integrity");
  const grid=node("div","integrity-grid");
  const rows=[
    ["Hash binding",d.integrity_gate?.hash_binding_ok],
    ["Bundle bytes",d.integrity_gate?.bundle_file_ok],
    ["Review document",d.integrity_gate?.review_document_ok],
    ["Episode media",d.integrity_gate?.episode_media_ok],
    ["QC current",d.integrity_gate?.qc_ok],
    ["Rights / provenance",d.integrity_gate?.provenance_ok],
  ];
  rows.forEach(([label,ok])=>{
    const box=node("div","integrity-item");
    box.appendChild(node("strong","",label));
    box.appendChild(pill(ok?"VERIFIED":"BLOCKED",ok?"PASSED":"FAILED"));
    grid.appendChild(box);
  });
  integrity.appendChild(grid);
  if((d.integrity_gate?.blocking_reasons||[]).length){
    const n=node("div","notice bad",
      "阻挡原因："+d.integrity_gate.blocking_reasons.join(", "));
    n.style.marginTop="10px";
    integrity.appendChild(n);
  }
  panel.append(card,integrity);
}

function renderQc(d){
  const panel=byId("tab-qc");
  clear(panel);
  const card=section("Deterministic Media QC");
  const grid=node("div","kv-grid");
  grid.append(
    kv("Report SHA-256",d.qc?.report_sha256||"—",true),
    kv("Passed",String(Boolean(d.qc?.passed))),
    kv("Hard failures",String(d.qc?.hard_failure_count??"—")),
    kv("Media SHA-256",d.media?.render_artifact_sha256||"—",true)
  );
  card.appendChild(grid);

  const checks=d.qc?.checks||[];
  const tableWrap=node("div","table-wrap");
  tableWrap.style.marginTop="12px";
  const table=node("table");
  const head=node("thead");
  const hr=node("tr");
  ["Check","Result","Observed","Expected"].forEach(x=>hr.appendChild(node("th","",x)));
  head.appendChild(hr);
  const body=node("tbody");
  checks.forEach(check=>{
    const tr=node("tr");
    tr.appendChild(node("td","",check.key));
    const result=node("td");
    result.appendChild(pill(check.passed?"PASS":"FAIL",check.passed?"PASSED":"FAILED"));
    tr.appendChild(result);
    tr.appendChild(node("td","code",JSON.stringify(check.observed??null)));
    tr.appendChild(node("td","code",JSON.stringify(check.expected??null)));
    body.appendChild(tr);
  });
  table.append(head,body);
  tableWrap.appendChild(table);
  card.appendChild(tableWrap);
  panel.appendChild(card);
}

function provenanceTable(title,data){
  const card=section(title);
  const header=node("div","kv-grid");
  header.append(
    kv("Artifact count",String(data?.artifact_count||0)),
    kv("All usage rights approved",String(Boolean(data?.all_usage_rights_approved)))
  );
  card.appendChild(header);
  const items=data?.items||[];
  items.forEach(item=>{
    const row=node("div","artifact-card");
    row.appendChild(node("div","",item.logical_key||"—"));
    const right=node("div");
    right.appendChild(pill(item.usage_rights||"—",item.usage_rights==="APPROVED"?"PASSED":"FAILED"));
    row.appendChild(right);
    row.appendChild(node("div","code",
      (item.license_id||"—")+" · "+shortHash(item.sha256)));
    card.appendChild(row);
  });
  return card;
}

function renderProvenance(d){
  const panel=byId("tab-provenance");
  clear(panel);
  panel.append(
    provenanceTable("Assets",d.provenance?.assets||{}),
    provenanceTable("Voices",d.provenance?.voices||{})
  );
}

function renderBundle(d){
  const panel=byId("tab-bundle");
  clear(panel);
  const hashes=section("Frozen Review Identity");
  const grid=node("div","kv-grid");
  grid.append(
    copyHash("Episode Bundle SHA-256",d.episode_bundle_sha256),
    copyHash("Release Review Package SHA-256",d.release_review_package_sha256),
    copyHash("Bundle Manifest SHA-256",d.bundle?.bundle_manifest_sha256),
    copyHash("Review Document SHA-256",d.review_package?.review_document_sha256)
  );
  hashes.appendChild(grid);

  const actions=node("div","download-actions");
  actions.style.marginTop="12px";
  const bundleLink=node("a","button link-button","下载 Episode Bundle");
  bundleLink.href=d.bundle_download_url;
  const docLink=node("a","button ghost link-button","打开 Review Markdown");
  docLink.href=d.review_document_url;
  docLink.target="_blank";
  docLink.rel="noopener";
  actions.append(bundleLink,docLink);
  hashes.appendChild(actions);
  panel.appendChild(hashes);

  const artifacts=section("Bundle Artifacts");
  (d.bundle?.artifact_manifest||[]).forEach(item=>{
    const row=node("div","artifact-card");
    row.appendChild(node("div","",item.relative_path));
    row.appendChild(node("div","muted",String(item.byte_size)+" bytes"));
    row.appendChild(node("div","code",item.sha256));
    artifacts.appendChild(row);
  });
  panel.appendChild(artifacts);

  const manifests=section("Manifest Lineage");
  (d.manifest_hashes||[]).forEach(item=>{
    const row=node("div","artifact-card");
    row.appendChild(node("div","",item.stage_key+" / "+item.manifest_kind));
    row.appendChild(node("div","muted","v"+item.manifest_version));
    row.appendChild(node("div","code",item.content_sha256));
    manifests.appendChild(row);
  });
  panel.appendChild(manifests);
}

function renderTranscript(d){
  const panel=byId("tab-transcript");
  clear(panel);
  const card=section("Frozen Transcript");
  (d.transcript||[]).forEach(item=>{
    const row=node("div","transcript-row");
    row.appendChild(node("div","code",item.scene_id+" · "+item.line_id));
    row.appendChild(node("div","",item.speaker));
    row.appendChild(node("div","",item.text));
    card.appendChild(row);
  });
  panel.appendChild(card);
}

function renderDecision(d){
  const panel=byId("tab-decision");
  clear(panel);

  const boundary=section("Human Decision Gate");
  const grid=node("div","kv-grid");
  grid.append(
    kv("Review status",d.review_status),
    kv("Can APPROVE",d.can_approve?"YES":"NO"),
    kv("Can REJECT",d.can_reject?"YES":"NO"),
    kv("Review key configured",d.human_review_key_configured?"YES":"NO")
  );
  boundary.appendChild(grid);
  const note=node(
    "div",
    "decision-boundary",
    "APPROVE 只记录 RELEASE_APPROVED，不会启用 publish_enabled、Production execution、部署、推广或回滚。"
  );
  note.style.marginTop="12px";
  boundary.appendChild(note);
  panel.appendChild(boundary);

  if((d.decisions||[]).length){
    const history=section("Decision History");
    const timeline=node("div","timeline");
    d.decisions.forEach(item=>{
      const box=node("div","timeline-item");
      const head=node("div","timeline-head");
      head.append(
        pill(item.decision,item.decision),
        pill(item.decision_status,item.decision_status),
        node("strong","",item.actor),
        node("span","muted",fmtDate(item.decided_at))
      );
      box.appendChild(head);
      box.appendChild(node("div","",item.reason));
      box.appendChild(node("div","candidate-hash",
        "decision "+shortHash(item.decision_sha256)));
      timeline.appendChild(box);
    });
    history.appendChild(timeline);
    panel.appendChild(history);
  }

  const formCard=section("Explicit APPROVE / REJECT");
  const form=node("div","decision-box");

  const checklist=node("div","field");
  checklist.appendChild(node("label","","Required checklist for APPROVE"));
  const list=node("div","checklist");
  const checkboxMap=new Map();
  (d.reviewer_checklist||[]).forEach(item=>{
    const label=node("label","check-item");
    const input=document.createElement("input");
    input.type="checkbox";
    input.disabled=d.review_status!=="READY_FOR_HUMAN_REVIEW";
    checkboxMap.set(item.key,input);
    const text=node("div");
    text.appendChild(node("strong","",item.label));
    text.appendChild(node("div","muted",item.key));
    label.append(input,text);
    list.appendChild(label);
  });
  checklist.appendChild(list);

  const keyField=node("div","field");
  keyField.appendChild(node("label","","X-Shrimp-Review-Key"));
  const key=document.createElement("input");
  key.type="password";
  key.autocomplete="off";
  key.spellcheck=false;
  key.placeholder="只用于本次请求，不保存";
  keyField.appendChild(key);

  const actorField=node("div","field");
  actorField.appendChild(node("label","","Reviewer"));
  const actor=document.createElement("input");
  actor.value="shrimp-human-review-ui";
  actor.maxLength=200;
  actorField.appendChild(actor);

  const reasonField=node("div","field");
  reasonField.appendChild(node("label","","Decision reason"));
  const reason=document.createElement("textarea");
  reason.maxLength=4000;
  reason.placeholder="记录批准或拒绝的具体理由（至少 3 个字符）";
  reasonField.appendChild(reason);

  const actions=node("div","decision-actions");
  const approve=node("button","button approve","APPROVE · RELEASE_APPROVED");
  approve.type="button";
  approve.disabled=!d.can_approve||!d.human_review_key_configured;
  const reject=node("button","button danger","REJECT · RELEASE_REJECTED");
  reject.type="button";
  reject.disabled=!d.can_reject||!d.human_review_key_configured;
  actions.append(approve,reject);

  async function submit(decision){
    if(reason.value.trim().length<3){
      showToast("请填写至少 3 个字符的理由",true);
      return;
    }
    if(!key.value){
      showToast("请输入 X-Shrimp-Review-Key",true);
      return;
    }
    const confirmed=[...checkboxMap.entries()]
      .filter(([,input])=>input.checked)
      .map(([name])=>name);
    if(decision==="APPROVE"){
      const required=d.required_checklist_keys||[];
      const missing=required.filter(x=>!confirmed.includes(x));
      if(missing.length){
        showToast("APPROVE 前必须完成全部 checklist："+missing.join(", "),true);
        return;
      }
    }
    const message=decision==="APPROVE"
      ? "确认 APPROVE 当前冻结的 Episode Bundle 与 Review Package？\n\n这只会记录 RELEASE_APPROVED，不会自动发布。"
      : "确认 REJECT 当前冻结的 Episode？";
    if(!window.confirm(message)) return;

    approve.disabled=true;
    reject.disabled=true;
    try{
      await api(
        "/v1/shrimp-animation/review-workspace/"+encodeURIComponent(d.job_id)+"/decision",
        {
          method:"POST",
          headers:{
            "Content-Type":"application/json",
            "X-Shrimp-Review-Key":key.value,
          },
          body:JSON.stringify({
            decision,
            reason:reason.value.trim(),
            actor:actor.value.trim()||"shrimp-human-review-ui",
            episode_bundle_sha256:d.episode_bundle_sha256,
            release_review_package_sha256:d.release_review_package_sha256,
            confirmed_checklist:confirmed,
          }),
        }
      );
      key.value="";
      reason.value="";
      showToast(
        decision==="APPROVE"
          ? "已记录 RELEASE_APPROVED；自动发布仍为 DISABLED。"
          : "已记录 RELEASE_REJECTED。"
      );
      await loadEpisodes();
      await selectEpisode(d.job_id,false);
    }catch(err){
      key.value="";
      showToast("决策被拒绝："+err.message,true);
      renderDecision(d);
    }
  }

  approve.addEventListener("click",()=>submit("APPROVE"));
  reject.addEventListener("click",()=>submit("REJECT"));
  form.append(checklist,keyField,actorField,reasonField,actions);
  formCard.appendChild(form);

  if(!d.human_review_key_configured){
    const warn=node("div","notice warn",
      "服务器尚未配置 SHRIMP_HUMAN_REVIEW_KEY，因此人工决策保持关闭。");
    warn.style.marginTop="10px";
    formCard.appendChild(warn);
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
  const id=state.selected?.job_id;
  await loadEpisodes();
  if(id) await selectEpisode(id,false);
  showToast("已重新整理");
});
window.addEventListener("popstate",async()=>{
  const prefix="/animation-review/";
  const id=location.pathname.startsWith(prefix)
    ? location.pathname.slice(prefix.length)
    : "";
  if(id) await selectEpisode(id,false);
  else{
    state.selected=null;
    renderEpisodes();
    byId("detailContent").classList.add("hidden");
    byId("emptyState").classList.remove("hidden");
  }
});

loadEpisodes();
