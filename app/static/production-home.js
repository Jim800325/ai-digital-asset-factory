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
    if (!response.ok) {
      const error = new Error("HTTP " + response.status);
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  };
  const listData = (data) => Array.isArray(data) ? data :
    (Array.isArray(data?.items) ? data.items : Array.isArray(data?.results) ? data.results : null);
  const showIssue = (id, message) => {
    const root = byId(id);
    if (!root) return;
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = message;
    root.replaceChildren(p);
  };
  const shortId = (value) => String(value || "—").slice(0, 12);
  const safeDate = (value) => {
    if (!value) return "时间未提供";
    const parsed = new Date(value);
    return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString("zh-CN", { hour12: false }) : "时间未知";
  };
  const item = (title, description, state) => {
    const row = document.createElement("div");
    row.className = "item";
    const info = document.createElement("div");
    const heading = document.createElement("div");
    heading.className = "item-title";
    heading.textContent = title;
    const meta = document.createElement("div");
    meta.className = "item-meta";
    meta.textContent = description;
    info.append(heading, meta);
    const badge = document.createElement("span");
    badge.className = "item-status";
    badge.textContent = state || "UNKNOWN";
    row.append(info, badge);
    return row;
  };

  const renderHealth = (data, failed = false) => {
    const node = byId("systemStatus");
    if (!node) return;
    const db = data?.database?.available === true;
    const status = !failed && data?.status === "ok" && db ? "READY" : "DEGRADED";
    node.textContent = status;
    node.className = "status " + (status === "READY" ? "ok" : "bad");
    setText("executionMode", data?.mode || "UNAVAILABLE");
    const migration = data?.migrations;
    const count = migration?.applied_count;
    const expected = migration?.expected_count;
    const counts = typeof count === "number" && typeof expected === "number" ?
      " · " + count + "/" + expected : "";
    setText("migrationStatus", (migration?.status || "UNAVAILABLE") + counts);
    setText("releaseGate", data?.release_deployment || "UNKNOWN");
  };

  const renderRuns = (data) => {
    const rows = listData(data);
    if (!rows) throw new Error("Unexpected runs response");
    setText("runCount", String(rows.length));
    setText("runsState", "DATA LOADED");
    if (!rows.length) return showIssue("runsList", "目前没有 Pipeline 运行记录。");
    byId("runsList").replaceChildren(...rows.slice(0, 5).map((entry) =>
      item("Run " + shortId(entry.id),
        safeDate(entry.started_at) + " · Evidence: " + (entry.evidence_created ?? "—"),
        String(entry.status || "UNKNOWN").toUpperCase())));
  };

  const renderOpportunities = (data) => {
    const rows = listData(data);
    if (!rows) throw new Error("Unexpected opportunities response");
    const realRows = rows.filter((entry) =>
      !String(entry.title || entry.canonical_title || "").startsWith("[TEST_ONLY]"));
    setText("opportunityCount", String(realRows.length));
    setText("opportunitiesState", "DATA LOADED");
    if (!realRows.length) return showIssue("opportunitiesList",
      rows.length ? "当前只有测试 Fixture，暂无真实机会研究条目。" : "当前没有机会研究条目。");
    byId("opportunitiesList").replaceChildren(...realRows.slice(0, 5).map((entry) =>
      item(String(entry.title || entry.canonical_title || "未命名机会"),
        "评分: " + (entry.score ?? "—") + " · 来源: " + (entry.independent_source_count ?? "—"),
        String(entry.status || "UNKNOWN").toUpperCase())));
  };

  const load = async () => {
    const button = byId("refreshButton");
    if (button) { button.disabled = true; button.textContent = "正在刷新…"; }
    setText("updatedAt", "正在读取 Production 的只读 API…");
    const tasks = [
      {path:"/health", done:renderHealth, failed:()=>renderHealth(null,true)},
      {path:"/v1/runs?limit=30", done:renderRuns, failed:()=>{setText("runCount","—");setText("runsState","UNAVAILABLE");showIssue("runsList","无法读取运行记录；请检查服务及数据库状态。");}},
      {path:"/v1/opportunities?limit=50", done:renderOpportunities, failed:()=>{setText("opportunityCount","—");setText("opportunitiesState","UNAVAILABLE");showIssue("opportunitiesList","无法读取机会列表；请检查服务及数据库状态。");}},
      {path:"/v1/review-workspace?limit=50", done:(data)=>{const list=listData(data);if(!list)throw Error("Invalid reviews");setText("reviewCount",list.length);},failed:()=>setText("reviewCount","—")},
      {path:"/v1/live-acceptance-audits?limit=100", done:(data)=>{const list=listData(data);if(!list)throw Error("Invalid audits");setText("auditCount",list.length);},failed:()=>setText("auditCount","—")}
    ];
    const results = await Promise.allSettled(tasks.map((task) => fetchJson(task.path)));
    let failed = 0;
    for (let i=0; i<results.length; i++) {
      const task = tasks[i], result = results[i];
      if (result.status === "fulfilled") {
        try { task.done(result.value); }
        catch (_) { failed++; task.failed(); }
      } else {
        failed++;
        task.failed();
      }
    }
    setText("updatedAt", "最后刷新：" + new Date().toLocaleString("zh-CN", {hour12:false}) +
      (failed ? " · " + failed + " 项数据不可用（已安全降级）" : " · 数据读取完成"));
    if (button) { button.disabled = false; button.textContent = "↻ 刷新状态"; }
  };
  byId("refreshButton")?.addEventListener("click", load);
  load();
})();
