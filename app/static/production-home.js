"use strict";
(() => {
  const byId = (id) => document.getElementById(id);
  const setText = (id, value) => {
    const node = byId(id);
    if (node) node.textContent = value === null || value === undefined ? "—" : String(value);
  };
  const fetchJson = async (path) => {
    const response = await fetch(path, { credentials: "same-origin", cache: "no-store" });
    let body = null;
    try { body = await response.json(); } catch (_) { /* preserve status */ }
    if (!response.ok) throw new Error("HTTP " + response.status);
    return body;
  };
  const safeDate = (value) => {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isFinite(parsed.getTime())
      ? parsed.toLocaleString("zh-HK", { hour12: false })
      : "—";
  };
  const shortId = (value) => String(value || "—").slice(0, 12);

  const actionCard = (entry) => {
    const card = document.createElement("article");
    card.className = "action-card";

    const priority = document.createElement("span");
    priority.className = "action-priority";
    priority.textContent = entry.priority || "INFO";

    const copy = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = entry.title || "下一步";
    const reason = document.createElement("p");
    reason.textContent = entry.reason || "—";
    copy.append(title, reason);

    const count = document.createElement("strong");
    count.className = "action-count";
    count.textContent = String(entry.count ?? 0);

    card.append(priority, copy, count);

    const href = String(entry.href || "");
    if (href) {
      const link = document.createElement("a");
      link.className = "action-link";
      link.href = href;
      link.textContent = "前往處理 →";
      card.append(link);
    } else {
      const next = document.createElement("span");
      next.className = "action-link";
      next.textContent = "目前沒有可導覽的處理入口";
      card.append(next);
    }
    return card;
  };

  const opportunityRow = (entry) => {
    const row = document.createElement("tr");

    const opportunity = document.createElement("td");
    const title = document.createElement("a");
    title.className = "opportunity-title";
    title.href = "/opportunities/" + encodeURIComponent(entry.id);
    title.textContent = entry.title || "未命名機會";
    const quality = document.createElement("div");
    quality.className = "opportunity-sub";
    quality.textContent =
      "Evidence quality " + (entry.evidence_quality_score ?? "—") +
      " · Signal " + (entry.signal_strength_score ?? "—");
    opportunity.append(title, quality);

    const type = document.createElement("td");
    type.textContent = entry.asset_type || "—";

    const score = document.createElement("td");
    score.className = "score";
    score.textContent = String(entry.score ?? "—");

    const sources = document.createElement("td");
    sources.textContent = String(entry.independent_source_count ?? "—");

    const gateCell = document.createElement("td");
    const gate = document.createElement("span");
    gate.className = "gate" + (entry.evidence_gate_passed ? " pass" : "");
    gate.textContent = entry.evidence_gate_passed ? "PASS" : "BLOCKED";
    gateCell.append(gate);

    const stage = document.createElement("td");
    stage.textContent = entry.stage || "—";

    const next = document.createElement("td");
    next.className = "next-action";
    next.textContent = String(entry.next_action || "REVIEW").replaceAll("_", " ");

    row.append(opportunity, type, score, sources, gateCell, stage, next);
    return row;
  };

  const render = (data) => {
    const funnel = data?.funnel || {};
    const metrics = data?.metrics || {};
    const systemData = data?.system || {};
    const bottleneck = data?.bottleneck || {};

    const system = byId("systemStatus");
    const ready = data?.status === "ok" && systemData.status === "READY";
    if (system) {
      system.textContent = ready ? "READY" : "DEGRADED";
      system.className = "status " + (ready ? "ok" : "bad");
    }

    setText("metricDiscovered", funnel.discovered);
    setText("metricEvidence", metrics.evidence_records);
    setText("metricCandidate", funnel.candidate);
    setText("metricBuildReady", funnel.build_ready);
    setText(
      "metricOpportunityMeta",
      (funnel.research ?? 0) + " RESEARCH · " + (funnel.watch ?? 0) + " WATCH"
    );
    setText(
      "metricEvidenceMeta",
      (metrics.evidence_blocked ?? 0) + " 個機會仍被 Gate 阻擋"
    );

    setText("funnelDiscovered", funnel.discovered);
    setText("funnelWatch", funnel.watch);
    setText("funnelResearch", funnel.research);
    setText("funnelCandidate", funnel.candidate);
    setText("funnelValidating", funnel.validating);
    setText("funnelBuildReady", funnel.build_ready);
    setText("funnelBuilding", funnel.building);
    setText("funnelPublished", funnel.published);
    setText("funnelProfitable", funnel.profitable);
    setText("publishedAvailability", data?.stage_availability?.published || "PLANNED");
    setText("profitAvailability", data?.stage_availability?.profitable || "PLANNED");
    setText("funnelState", "LIVE DATA");

    const stageLabel = String(bottleneck.stage || "DISCOVERY").replaceAll("_", " ");
    setText("bottleneckTitle", stageLabel);
    setText("bottleneckReason", bottleneck.reason || "目前沒有明確業務阻塞。");
    setText("bottleneckCount", bottleneck.count ?? 0);

    const blocked = Number(metrics.evidence_blocked || 0);
    const discovered = Number(funnel.discovered || 0);
    if (blocked > 0) {
      setText(
        "businessHeadline",
        "目前有 " + discovered + " 個機會，" + blocked + " 個仍卡在 Evidence Gate"
      );
      setText(
        "businessSummary",
        "當前最重要工作不是增加技術模組，而是為高分機會補充第二個獨立來源，讓真實 CANDIDATE 出現。"
      );
    } else {
      setText(
        "businessHeadline",
        "目前有 " + discovered + " 個機會，正在向下一個商業 Gate 推進"
      );
      setText("businessSummary", bottleneck.reason || "請依 Action Center 處理下一步。");
    }

    const actions = Array.isArray(data?.actions) ? data.actions : [];
    const actionRoot = byId("actionsList");
    if (actionRoot) {
      if (actions.length) actionRoot.replaceChildren(...actions.map(actionCard));
      else {
        const empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent = "目前沒有待處理 Action。";
        actionRoot.replaceChildren(empty);
      }
    }
    setText("actionsState", actions.length + " ACTION");

    const run = data?.latest_run;
    if (run) {
      setText("runState", "DATA LOADED");
      setText("latestRunId", shortId(run.id));
      setText("latestRunStatus", String(run.status || "UNKNOWN").toUpperCase());
      setText("latestRunCrawled", run.pages_crawled);
      setText("latestRunEvidence", run.evidence_created);
      setText("latestRunOpportunities", run.opportunities_created);
      setText("latestRunFinished", safeDate(run.finished_at));
    } else {
      setText("runState", "NO RUN");
    }

    const opportunities = Array.isArray(data?.top_opportunities) ? data.top_opportunities : [];
    const tbody = byId("opportunitiesTableBody");
    if (tbody) {
      if (opportunities.length) {
        tbody.replaceChildren(...opportunities.map(opportunityRow));
      } else {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 7;
        td.className = "table-empty";
        td.textContent = "目前沒有真實 Opportunity。";
        tr.append(td);
        tbody.replaceChildren(tr);
      }
    }
    setText("opportunitiesState", opportunities.length + " TOP");

    setText("systemDatabase", systemData.database || "—");
    setText("systemMigrations", systemData.migrations || "—");
    setText("systemPipeline", systemData.pipeline || "—");
    setText("systemWorker", systemData.worker_count ?? "—");
  };

  const fail = () => {
    const system = byId("systemStatus");
    if (system) {
      system.textContent = "DEGRADED";
      system.className = "status bad";
    }
    setText("businessHeadline", "副業工作台暫時無法讀取安全摘要");
    setText("businessSummary", "頁面保持唯讀與 fail-closed；請到 System Workspace 檢查平台狀態。");
    for (const id of [
      "metricDiscovered","metricEvidence","metricCandidate","metricBuildReady",
      "funnelDiscovered","funnelWatch","funnelResearch","funnelCandidate",
      "funnelValidating","funnelBuildReady","funnelBuilding","funnelPublished",
      "funnelProfitable","bottleneckCount"
    ]) setText(id, "—");
    setText("funnelState", "UNAVAILABLE");
    setText("actionsState", "UNAVAILABLE");
    setText("runState", "UNAVAILABLE");
    setText("opportunitiesState", "UNAVAILABLE");
  };

  const load = async () => {
    const button = byId("refreshButton");
    if (button) {
      button.disabled = true;
      button.textContent = "正在重新整理…";
    }
    setText("updatedAt", "正在讀取 Workbench Summary…");
    try {
      render(await fetchJson("/v1/workbench-summary"));
      setText(
        "updatedAt",
        "最後更新：" + new Date().toLocaleString("zh-HK", { hour12: false })
      );
    } catch (_) {
      fail();
      setText(
        "updatedAt",
        "最後更新：" + new Date().toLocaleString("zh-HK", { hour12: false }) + " · 安全降級"
      );
    }
    if (button) {
      button.disabled = false;
      button.textContent = "↻ 重新整理";
    }
  };

  const navLinks = [...document.querySelectorAll(".side-nav a[href^='#']")];
  const updateCurrentNav = () => {
    const sections = navLinks
      .map((link) => document.querySelector(link.getAttribute("href")))
      .filter(Boolean);
    let currentId = sections[0]?.id || "overview";
    for (const section of sections) {
      if (section.getBoundingClientRect().top <= 150) currentId = section.id;
    }
    for (const link of navLinks) {
      link.classList.toggle("current", link.getAttribute("href") === "#" + currentId);
    }
  };

  window.addEventListener("scroll", updateCurrentNav, { passive: true });
  window.addEventListener("resize", updateCurrentNav);
  byId("refreshButton")?.addEventListener("click", load);
  updateCurrentNav();
  load();
})();