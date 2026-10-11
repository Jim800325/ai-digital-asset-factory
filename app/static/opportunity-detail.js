"use strict";
(() => {
  const byId = (id) => document.getElementById(id);
  const setText = (id, value) => {
    const node = byId(id);
    if (node) node.textContent = value === null || value === undefined ? "—" : String(value);
  };
  const fetchJson = async (path) => {
    const response = await fetch(path, {credentials:"same-origin", cache:"no-store"});
    let body = null;
    try { body = await response.json(); } catch (_) { /* preserve status */ }
    if (!response.ok) throw new Error("HTTP " + response.status);
    return body;
  };
  const safeUrl = (value) => {
    try {
      const url = new URL(String(value || ""), window.location.origin);
      return ["http:","https:"].includes(url.protocol) ? url.href : "";
    } catch (_) {
      return "";
    }
  };
  const safeDate = (value) => {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString("zh-HK",{hour12:false}) : "—";
  };
  const label = (value) => String(value || "—").replaceAll("_"," ");

  const opportunityId = () => {
    const parts = window.location.pathname.split("/").filter(Boolean);
    return parts[0] === "opportunities" && parts[1] ? parts[1] : "";
  };

  const renderLifecycle = (items) => {
    const root = byId("lifecycle");
    if (!root) return;
    const nodes = (items || []).map((item,index) => {
      const card = document.createElement("div");
      const state = String(item.status || "PENDING").toLowerCase();
      card.className = "life-step " + (
        state === "done" || state === "approved" ? "done" :
        state === "current" || state === "pending_approval" ? "current" :
        state === "blocked" || state === "rejected" ? "blocked" : ""
      );
      const n = document.createElement("span");
      n.textContent = String(index + 1).padStart(2,"0") + " · " + label(item.stage);
      const s = document.createElement("strong");
      s.textContent = label(item.status);
      card.append(n,s);
      return card;
    });
    root.replaceChildren(...nodes);
  };

  const renderWorth = (items) => {
    const root = byId("worthList");
    if (!root) return;
    if (!(items || []).length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "目前沒有高於 70 分的突出維度。";
      root.replaceChildren(empty);
      return;
    }
    root.replaceChildren(...items.map((item) => {
      const card = document.createElement("div");
      card.className = "worth-card";
      const name = document.createElement("span");
      name.textContent = item.label;
      const score = document.createElement("strong");
      score.textContent = String(item.score);
      card.append(name,score);
      return card;
    }));
  };

  const renderScorecard = (scores) => {
    const root = byId("scorecard");
    if (!root) return;
    const mapping = [
      ["Demand","demand"],["Repeatability","repeatability"],["Automation","automation"],
      ["Ownership","ownership"],["Margin","marginal_cost"],["Evidence","evidence"]
    ];
    root.replaceChildren(...mapping.map(([name,key]) => {
      const card = document.createElement("div");
      card.className = "score-item";
      const span = document.createElement("span");
      span.textContent = name;
      const strong = document.createElement("strong");
      strong.textContent = String(scores?.[key] ?? "—");
      card.append(span,strong);
      return card;
    }));
  };

  const renderGate = (gate) => {
    const state = byId("gateState");
    if (state) {
      state.textContent = gate?.passed ? "PASS" : "BLOCKED";
      state.className = "badge " + (gate?.passed ? "pass" : "blocked");
    }
    const headerGate = byId("gateBadge");
    if (headerGate) {
      headerGate.textContent = "GATE " + (gate?.passed ? "PASS" : "BLOCKED");
      headerGate.className = "badge " + (gate?.passed ? "pass" : "blocked");
    }
    const root = byId("gateChecks");
    if (!root) return;
    root.replaceChildren(...(gate?.checks || []).map((item) => {
      const row = document.createElement("div");
      row.className = "gate-row " + (item.passed ? "pass" : "fail");
      const name = document.createElement("span");
      name.textContent = item.label;
      const values = document.createElement("small");
      values.textContent = "實際 " + item.actual + " / 門檻 " + item.required;
      const result = document.createElement("strong");
      result.textContent = item.passed ? "PASS" : "BLOCKED";
      row.append(name,values,result);
      return row;
    }));
  };

  const renderEvidence = (evidence) => {
    const summary = evidence?.summary || {};
    setText(
      "evidenceSummary",
      (summary.records || 0) + " 筆 Evidence · " +
      (summary.independent_sources || 0) + " 個獨立來源 · " +
      (summary.domains || []).length + " 個 Domain"
    );

    const classRoot = byId("sourceClassSummary");
    if (classRoot) {
      const entries = Object.entries(summary.source_classes || {});
      classRoot.replaceChildren(...entries.map(([name,count]) => {
        const chip = document.createElement("span");
        chip.className = "source-chip";
        chip.textContent = name + " · " + count;
        return chip;
      }));
    }

    const root = byId("evidenceItems");
    if (!root) return;
    const items = evidence?.items || [];
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "目前沒有 Evidence 記錄。";
      root.replaceChildren(empty);
      return;
    }
    root.replaceChildren(...items.map((item) => {
      const card = document.createElement("article");
      card.className = "evidence-card";
      const top = document.createElement("div");
      top.className = "evidence-top";
      const title = document.createElement("strong");
      title.textContent = item.document_title || item.signal_type || "Evidence";
      const meta = document.createElement("span");
      meta.textContent = (item.source_class || "unknown") + " · " + safeDate(item.discovered_at);
      top.append(title,meta);

      const excerpt = document.createElement("p");
      excerpt.textContent = item.excerpt || "—";

      const metrics = document.createElement("div");
      metrics.className = "evidence-metrics";
      for (const value of [
        "Quality " + item.source_quality,
        "Signal " + item.signal_strength,
        "Confidence " + item.confidence
      ]) {
        const span = document.createElement("span");
        span.textContent = value;
        metrics.append(span);
      }

      card.append(top,excerpt,metrics);
      const url = safeUrl(item.source_url);
      if (url) {
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = (item.source_domain || "來源") + " ↗";
        card.append(link);
      }
      return card;
    }));
  };

  const fieldCard = (name,value) => {
    const card = document.createElement("div");
    card.className = "field-card";
    const span = document.createElement("span");
    span.textContent = name;
    const p = document.createElement("p");
    p.textContent = value || "—";
    card.append(span,p);
    return card;
  };

  const renderResearch = (report) => {
    const root = byId("researchBody");
    if (!root) return;
    if (!report) {
      setText("researchStatus","NOT GENERATED");
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "尚未生成 Research Report；必须先通过 Evidence Gate 进入 CANDIDATE。";
      root.replaceChildren(empty);
      return;
    }
    setText("researchStatus",report.report_status || "—");
    root.replaceChildren(
      fieldCard("Problem",report.problem),
      fieldCard("Buyer",report.buyer),
      fieldCard("Alternatives",report.existing_alternatives),
      fieldCard("Monetization",report.monetization),
      fieldCard("Build Complexity",report.build_complexity),
      fieldCard("Risks",report.risks),
      fieldCard("Why Now",report.why_now)
    );
  };

  const validationItem = (name,value) => {
    const card = document.createElement("div");
    card.className = "validation-item";
    const span = document.createElement("span");
    span.textContent = name;
    const strong = document.createElement("strong");
    strong.textContent = value || "UNKNOWN";
    const state = String(value || "").toLowerCase();
    if (state === "validated") strong.className = "validated";
    if (state === "partial") strong.className = "partial";
    card.append(span,strong);
    return card;
  };

  const renderValidation = (validation) => {
    const root = byId("validationBody");
    if (!root) return;
    if (!validation) {
      setText("validationStatus","NOT STARTED");
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "尚未进入商業驗證；Research Report 生成后才会执行。";
      root.replaceChildren(empty);
      return;
    }
    setText("validationStatus",validation.validation_status || "—");
    const grid = document.createElement("div");
    grid.className = "validation-grid";
    grid.append(
      validationItem("Buyer",validation.buyer_status),
      validationItem("Competitors",validation.competitors_status),
      validationItem("Pricing",validation.pricing_status),
      validationItem("Willingness to Pay",validation.willingness_to_pay_status),
      validationItem("Market Gap",validation.market_gap_status),
      validationItem("Completeness",String(validation.completeness_score ?? "—"))
    );
    root.replaceChildren(grid);
  };

  const renderBuild = (opportunity,proposal) => {
    const badge = byId("buildBadge");
    const ready = opportunity?.build_readiness === "BUILD_READY";
    if (badge) {
      badge.textContent = opportunity?.build_readiness || "NOT_READY";
      badge.className = "badge " + (ready ? "pass" : "blocked");
    }
    const root = byId("buildBody");
    if (!root) return;
    if (!proposal) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = ready
        ? "BUILD_READY 已满足，但目前尚未生成 Build Proposal。"
        : "尚未满足 BUILD_READY；不会生成 Build Proposal。";
      root.replaceChildren(empty);
      return;
    }
    root.replaceChildren(
      fieldCard("Proposal Status",proposal.proposal_status),
      fieldCard("Artifact Type",proposal.artifact_type),
      fieldCard("Objective",proposal.objective),
      fieldCard("Stack",Array.isArray(proposal.proposed_stack) ? proposal.proposed_stack.join(" · ") : String(proposal.proposed_stack || "—"))
    );
  };

  const renderAliases = (aliases) => {
    const root = byId("aliases");
    if (!root) return;
    if (!(aliases || []).length) {
      const empty = document.createElement("span");
      empty.className = "muted";
      empty.textContent = "目前沒有 Alias / merged-title 記錄。";
      root.replaceChildren(empty);
      return;
    }
    root.replaceChildren(...aliases.map((item) => {
      const chip = document.createElement("span");
      chip.className = "alias-chip";
      chip.textContent = item.alias || "—";
      return chip;
    }));
  };

  const render = (data) => {
    const op = data.opportunity || {};
    setText("opportunityTitle",op.title || "未命名 Opportunity");
    setText("opportunityProblem",op.problem || "尚未提供 Problem 描述。");
    setText("stageBadge",label(op.stage));
    setText("scoreValue",op.score);
    setText("assetType",label(op.asset_type));
    setText("sourceCount",op.independent_source_count);
    setText("evidenceCount",op.evidence_count);
    setText("validationScore",op.research_validation_score);
    setText("buildReadiness",op.build_readiness);
    setText("updatedAt","最後更新 " + safeDate(op.updated_at));

    const stageBadge = byId("stageBadge");
    if (stageBadge) stageBadge.className = "badge neutral";

    const next = data.next_action || {};
    setText("nextActionLabel",next.label || next.code || "—");
    setText("nextActionReason",next.reason || "—");
    setText("nextActionTarget",next.target || "—");

    const primary = byId("primarySourceLink");
    const sourceUrl = safeUrl(op.source_url);
    if (primary) {
      if (sourceUrl) {
        primary.href = sourceUrl;
        primary.hidden = false;
      } else {
        primary.hidden = true;
      }
    }

    renderLifecycle(data.lifecycle);
    renderWorth(data.why_worth_attention);
    renderScorecard(data.scorecard);
    renderGate(data.gate);
    renderEvidence(data.evidence);
    renderResearch(data.research);
    renderValidation(data.validation);
    renderBuild(op,data.build_proposal);
    renderAliases(data.aliases);
  };

  const fail = () => {
    setText("opportunityTitle","Opportunity 暫時不可用");
    setText("opportunityProblem","頁面保持唯讀與 fail-closed；請返回機會池稍後重試。");
    setText("stageBadge","UNAVAILABLE");
    setText("gateBadge","GATE —");
  };

  const id = opportunityId();
  if (!id) {
    fail();
    return;
  }
  fetchJson("/v1/opportunity-workspace/" + encodeURIComponent(id))
    .then(render)
    .catch(fail);
})();