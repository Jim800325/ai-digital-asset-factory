"use strict";
(() => {
  const PAGE_SIZE = 25;
  let currentOffset = 0;
  let lastPayload = null;

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
  const appendTextCell = (row, textValue, className) => {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = textValue === null || textValue === undefined ? "—" : String(textValue);
    row.append(cell);
    return cell;
  };
  const stageLabel = (value) => String(value || "—").replaceAll("_", " ");
  const blockerLabel = (code) => ({
    INDEPENDENT_SOURCES:"獨立來源",
    EVIDENCE_QUALITY:"Evidence Quality",
    SOURCE_DIVERSITY:"Source Diversity",
    SIGNAL_STRENGTH:"Signal Strength",
    CANDIDATE_SCORE:"Score"
  }[code] || code);

  const opportunityRow = (item) => {
    const row = document.createElement("tr");

    const titleCell = document.createElement("td");
    const link = document.createElement("a");
    link.className = "opportunity-link";
    link.href = "/opportunities/" + encodeURIComponent(item.id);
    link.textContent = item.title || "未命名機會";
    const subline = document.createElement("div");
    subline.className = "subline";
    subline.textContent = "Quality " + item.evidence_quality_score + " · Signal " + item.signal_strength_score;
    titleCell.append(link, subline);
    row.append(titleCell);

    appendTextCell(row, stageLabel(item.asset_type));
    appendTextCell(row, item.score, "score");
    appendTextCell(row, item.independent_source_count);

    const evidenceCell = document.createElement("td");
    evidenceCell.textContent = String(item.evidence_count ?? 0);
    const blockers = document.createElement("div");
    blockers.className = "blockers";
    blockers.textContent = (item.gate_failed_checks || []).map(blockerLabel).join(" · ") || "門檻已滿足";
    evidenceCell.append(blockers);
    row.append(evidenceCell);

    const gateCell = document.createElement("td");
    const gate = document.createElement("span");
    gate.className = "badge " + (item.evidence_gate_passed ? "pass" : "blocked");
    gate.textContent = item.evidence_gate_passed ? "PASS" : "BLOCKED";
    gateCell.append(gate);
    row.append(gateCell);

    appendTextCell(row, stageLabel(item.stage));

    const next = appendTextCell(row, item.next_action?.label || item.next_action?.code || "—", "next-action");
    next.title = item.next_action?.reason || "";
    return row;
  };

  const opportunityCard = (item) => {
    const card = document.createElement("article");
    card.className = "op-card";

    const head = document.createElement("div");
    head.className = "op-card-head";
    const h3 = document.createElement("h3");
    const link = document.createElement("a");
    link.href = "/opportunities/" + encodeURIComponent(item.id);
    link.textContent = item.title || "未命名機會";
    h3.append(link);
    const score = document.createElement("strong");
    score.className = "op-card-score";
    score.textContent = String(item.score ?? "—");
    head.append(h3, score);

    const meta = document.createElement("div");
    meta.className = "op-card-meta";
    for (const value of [
      stageLabel(item.asset_type),
      stageLabel(item.stage),
      "Sources " + item.independent_source_count,
      item.evidence_gate_passed ? "Gate PASS" : "Gate BLOCKED"
    ]) {
      const chip = document.createElement("span");
      chip.textContent = value;
      meta.append(chip);
    }

    const next = document.createElement("div");
    next.className = "op-card-next";
    next.textContent = "下一步：" + (item.next_action?.label || "檢查機會");

    card.append(head, meta, next);
    return card;
  };

  const queryParams = () => {
    const params = new URLSearchParams();
    const mapping = [
      ["q","filterQuery"],
      ["stage","filterStage"],
      ["asset_type","filterAssetType"],
      ["gate","filterGate"],
      ["min_score","filterMinScore"],
      ["min_sources","filterMinSources"],
      ["sort","filterSort"],
      ["order","filterOrder"]
    ];
    for (const [key,id] of mapping) {
      const value = String(byId(id)?.value || "").trim();
      if (value) params.set(key, value);
    }
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(currentOffset));
    return params;
  };

  const renderFacets = (payload) => {
    const facets = payload.facets || {};
    const stages = facets.stages || {};
    const gate = facets.gate || {};
    setText("totalCount", (stages.RESEARCH || 0) + (stages.WATCH || 0) + (stages.CANDIDATE || 0));
    setText("researchCount", stages.RESEARCH || 0);
    setText("watchCount", stages.WATCH || 0);
    setText("gatePassCount", gate.PASS || 0);
    setText("gateBlockedCount", gate.BLOCKED || 0);
  };

  const renderActiveFilters = (filters) => {
    const root = byId("activeFilters");
    if (!root) return;
    const entries = Object.entries(filters || {}).filter(([key,value]) =>
      value !== null && value !== "" && !["sort","order"].includes(key)
    );
    const nodes = entries.map(([key,value]) => {
      const chip = document.createElement("span");
      chip.className = "filter-chip";
      chip.textContent = key.replaceAll("_"," ") + ": " + value;
      return chip;
    });
    root.replaceChildren(...nodes);
  };

  const render = (payload) => {
    lastPayload = payload;
    const items = Array.isArray(payload.items) ? payload.items : [];
    const pagination = payload.pagination || {};
    renderFacets(payload);
    renderActiveFilters(payload.filters);

    setText("loadState", "DATA LOADED");
    setText("resultCount", pagination.total ?? 0);
    setText(
      "resultSummary",
      items.length
        ? "預設依最可行動順序排列；點擊任何 Opportunity 查看完整 Lifecycle。"
        : "目前沒有符合條件的真實 Opportunity。"
    );

    const rows = byId("opportunityRows");
    const cards = byId("opportunityCards");
    if (rows) {
      if (items.length) rows.replaceChildren(...items.map(opportunityRow));
      else {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 8;
        td.className = "empty-cell";
        td.textContent = "沒有符合條件的 Opportunity。";
        tr.append(td);
        rows.replaceChildren(tr);
      }
    }
    if (cards) cards.replaceChildren(...items.map(opportunityCard));

    const page = Math.floor((pagination.offset || 0) / PAGE_SIZE) + 1;
    const pages = Math.max(1, Math.ceil((pagination.total || 0) / PAGE_SIZE));
    setText("pageState", "第 " + page + " / " + pages + " 頁");
    const prev = byId("prevPage");
    const next = byId("nextPage");
    if (prev) prev.disabled = (pagination.offset || 0) <= 0;
    if (next) next.disabled = !pagination.has_more;
  };

  const load = async () => {
    const refresh = byId("refreshButton");
    if (refresh) { refresh.disabled = true; refresh.textContent = "讀取中…"; }
    setText("loadState", "CHECKING");
    try {
      const payload = await fetchJson("/v1/opportunity-workspace?" + queryParams().toString());
      render(payload);
    } catch (_) {
      setText("loadState", "UNAVAILABLE");
      setText("resultCount", "—");
      setText("resultSummary", "Opportunity Workspace 暫時不可用；頁面保持唯讀與 fail-closed。");
      byId("opportunityRows")?.replaceChildren();
      byId("opportunityCards")?.replaceChildren();
    } finally {
      if (refresh) { refresh.disabled = false; refresh.textContent = "↻ 重新整理"; }
    }
  };

  byId("filterForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    currentOffset = 0;
    load();
  });
  byId("resetFilters")?.addEventListener("click", () => {
    byId("filterForm")?.reset();
    currentOffset = 0;
    load();
  });
  byId("prevPage")?.addEventListener("click", () => {
    currentOffset = Math.max(0, currentOffset - PAGE_SIZE);
    load();
  });
  byId("nextPage")?.addEventListener("click", () => {
    if (lastPayload?.pagination?.has_more) {
      currentOffset += PAGE_SIZE;
      load();
    }
  });
  byId("refreshButton")?.addEventListener("click", load);
  load();
})();