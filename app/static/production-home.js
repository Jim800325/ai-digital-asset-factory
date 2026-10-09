"use strict";
(() => {
  const byId = (id) => document.getElementById(id);
  const setText = (id, value) => {
    const node = byId(id);
    if (node) node.textContent = value === null || value === undefined ? "—" : String(value);
  };
  const fetchJson = async (path, options = {}) => {
    const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
    let body = null;
    try { body = await response.json(); } catch (_) { /* preserve status */ }
    if (!response.ok) throw new Error("HTTP " + response.status);
    return body;
  };
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
    if (!value) return "未提供時間";
    const parsed = new Date(value);
    return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString("zh-HK", { hour12: false }) : "時間不明";
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

  const renderSummary = (data) => {
    const dbReady = data?.database?.available === true;
    const system = byId("systemStatus");
    const state = data?.status === "ok" && dbReady ? "READY" : "DEGRADED";
    if (system) {
      system.textContent = state;
      system.className = "status " + (state === "READY" ? "ok" : "bad");
    }

    setText("executionMode", data?.mode || "UNAVAILABLE");
    const migration = data?.migrations || {};
    const counts = typeof migration.applied_count === "number" &&
      typeof migration.expected_count === "number"
      ? " · " + migration.applied_count + "/" + migration.expected_count : "";
    setText("migrationStatus", (migration.status || "UNAVAILABLE") + counts);
    setText("releaseGate", data?.release_deployment || "UNKNOWN");

    const metrics = data?.metrics || {};
    setText("runCount", metrics.pipeline_runs);
    setText("opportunityCount", metrics.opportunities);
    setText("reviewCount", metrics.human_reviews);
    setText("auditCount", metrics.audit_records);

    const runs = Array.isArray(data?.recent_runs) ? data.recent_runs : [];
    setText("runsState", "DATA LOADED");
    if (!runs.length) {
      showIssue("runsList", "目前沒有 Pipeline 執行記錄。");
    } else {
      byId("runsList").replaceChildren(...runs.map((entry) =>
        item("Run " + shortId(entry.id),
          safeDate(entry.started_at) + " · Evidence: " + (entry.evidence_created ?? "—"),
          String(entry.status || "UNKNOWN").toUpperCase())));
    }

    const opportunities = Array.isArray(data?.recent_opportunities) ? data.recent_opportunities : [];
    setText("opportunitiesState", "DATA LOADED");
    if (!opportunities.length) {
      showIssue("opportunitiesList", "目前沒有真實機會研究項目。");
    } else {
      byId("opportunitiesList").replaceChildren(...opportunities.map((entry) =>
        item(String(entry.title || "未命名機會"),
          "評分: " + (entry.score ?? "—") + " · 來源: " + (entry.independent_source_count ?? "—"),
          String(entry.status || "UNKNOWN").toUpperCase())));
    }
  };

  const renderFailure = () => {
    const system = byId("systemStatus");
    if (system) {
      system.textContent = "DEGRADED";
      system.className = "status bad";
    }
    for (const id of ["runCount","opportunityCount","reviewCount","auditCount"]) setText(id, "—");
    setText("runsState", "UNAVAILABLE");
    setText("opportunitiesState", "UNAVAILABLE");
    showIssue("runsList", "無法讀取安全摘要；請檢查服務及資料庫狀態。");
    showIssue("opportunitiesList", "無法讀取安全摘要；請檢查服務及資料庫狀態。");
  };

  const load = async () => {
    const button = byId("refreshButton");
    if (button) { button.disabled = true; button.textContent = "正在重新整理…"; }
    setText("updatedAt", "正在讀取 Production 安全摘要…");
    try {
      renderSummary(await fetchJson("/v1/control-center-summary"));
      setText("updatedAt", "最後更新：" + new Date().toLocaleString("zh-HK", {hour12:false}) + " · 安全摘要讀取完成");
    } catch (_) {
      renderFailure();
      setText("updatedAt", "最後更新：" + new Date().toLocaleString("zh-HK", {hour12:false}) + " · 安全摘要不可用（已安全降級）");
    }
    if (button) { button.disabled = false; button.textContent = "↻ 重新整理狀態"; }
  };


  const shell = document.querySelector(".app-shell");
  const toggle = byId("sidebarToggle");
  const navLinks = [...document.querySelectorAll(".side-nav a[href^='#']")];

  const setSidebar = (collapsed) => {
    shell?.classList.toggle("sidebar-collapsed", collapsed);
    if (toggle) {
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.setAttribute("title", collapsed ? "展開側欄" : "摺疊側欄");
    }
  };

  toggle?.addEventListener("click", () => {
    setSidebar(!shell?.classList.contains("sidebar-collapsed"));
  });

  const updateCurrentNav = () => {
    const sections = navLinks
      .map((link) => document.querySelector(link.getAttribute("href")))
      .filter(Boolean);
    let currentId = sections[0]?.id || "overview";
    for (const section of sections) {
      if (section.getBoundingClientRect().top <= 140) currentId = section.id;
    }
    for (const link of navLinks) {
      link.classList.toggle("current", link.getAttribute("href") === "#" + currentId);
    }
  };

  window.addEventListener("scroll", updateCurrentNav, { passive: true });
  window.addEventListener("resize", () => {
    if (window.innerWidth <= 860) setSidebar(false);
    updateCurrentNav();
  });
  updateCurrentNav();


  const manualRunButton = byId("manualRunButton");
  const manualRunDialog = byId("manualRunDialog");
  const manualRunKeyInput = byId("manualRunKey");
  const manualRunConfirm = byId("manualRunConfirm");
  const manualRunStatus = byId("manualRunStatus");
  const manualRunDialogMessage = byId("manualRunDialogMessage");
  let activeManualJobId = null;
  let manualPollTimer = null;

  const setManualStatus = (message, state = "") => {
    if (!manualRunStatus) return;
    manualRunStatus.className = "manual-run-status" + (state ? " " + state : "");
    manualRunStatus.replaceChildren(document.createTextNode(message));
  };

  const checkManualReadiness = async () => {
    if (!manualRunButton) return;
    manualRunButton.disabled = true;
    try {
      const readiness = await fetchJson("/v1/manual-pipeline/readiness");
      if (readiness?.status === "READY") {
        manualRunButton.disabled = false;
        setManualStatus(
          "手動執行已就緒 · Redis 已連線 · Worker " +
          String(readiness.active_worker_count ?? 0) + " 個",
          "ready"
        );
      } else {
        const blockers = Array.isArray(readiness?.blockers) ? readiness.blockers.join(" · ") : "UNKNOWN";
        setManualStatus("手動執行目前已封鎖 · " + blockers);
      }
    } catch (_) {
      setManualStatus("無法確認手動執行條件；為安全起見保持封鎖。");
    }
  };

  const stopManualPolling = () => {
    if (manualPollTimer) window.clearTimeout(manualPollTimer);
    manualPollTimer = null;
  };

  const pollManualJob = async () => {
    if (!activeManualJobId) return;
    try {
      const job = await fetchJson("/v1/manual-pipeline/jobs/" + encodeURIComponent(activeManualJobId));
      const status = String(job?.status || "UNKNOWN").toUpperCase();
      if (status === "FINISHED") {
        stopManualPolling();
        activeManualJobId = null;
        const result = job?.result || {};
        const message =
          "執行完成 · Run " + shortId(job?.run_id) +
          " · Evidence " + String(result.evidence ?? "—") +
          " · Opportunities " + String(result.opportunities ?? "—");
        setManualStatus(message, "ready");
        if (manualRunStatus) {
          const link = document.createElement("a");
          link.href = "#opportunities";
          link.textContent = "查看 Evidence →";
          manualRunStatus.append(link);
        }
        await load();
        await checkManualReadiness();
        return;
      }
      if (["FAILED", "STOPPED", "CANCELED"].includes(status)) {
        stopManualPolling();
        activeManualJobId = null;
        setManualStatus("手動 Pipeline 執行失敗或已停止。請查看 Worker / Pipeline 記錄。", "failed");
        await load();
        await checkManualReadiness();
        return;
      }
      setManualStatus("Pipeline 正在執行 · Job " + shortId(activeManualJobId) + " · " + status, "running");
      manualPollTimer = window.setTimeout(pollManualJob, 2000);
    } catch (_) {
      setManualStatus("暫時無法讀取 Job 狀態，將繼續重試。", "running");
      manualPollTimer = window.setTimeout(pollManualJob, 3000);
    }
  };

  manualRunButton?.addEventListener("click", () => {
    if (manualRunDialogMessage) manualRunDialogMessage.textContent = "";
    if (manualRunKeyInput) manualRunKeyInput.value = "";
    manualRunDialog?.showModal();
    window.setTimeout(() => manualRunKeyInput?.focus(), 0);
  });

  manualRunConfirm?.addEventListener("click", async () => {
    const key = manualRunKeyInput?.value || "";
    if (!key) {
      if (manualRunDialogMessage) manualRunDialogMessage.textContent = "請輸入操作金鑰。";
      return;
    }
    manualRunConfirm.disabled = true;
    if (manualRunDialogMessage) manualRunDialogMessage.textContent = "正在建立 Pipeline Run…";
    try {
      const created = await fetchJson("/v1/runs", {
        method: "POST",
        headers: { "X-Manual-Run-Key": key }
      });
      if (manualRunKeyInput) manualRunKeyInput.value = "";
      activeManualJobId = created?.job_id || null;
      manualRunDialog?.close();
      manualRunButton.disabled = true;
      setManualStatus("Pipeline 已排入佇列 · Job " + shortId(activeManualJobId), "running");
      if (activeManualJobId) pollManualJob();
    } catch (error) {
      if (manualRunKeyInput) manualRunKeyInput.value = "";
      if (manualRunDialogMessage) {
        manualRunDialogMessage.textContent =
          error?.message === "HTTP 403"
            ? "操作金鑰不正確。"
            : "目前無法建立 Pipeline Run；請確認 readiness 與 Worker 狀態。";
      }
    } finally {
      manualRunConfirm.disabled = false;
    }
  });

  byId("refreshButton")?.addEventListener("click", async () => { await load(); await checkManualReadiness(); });
  load();
  checkManualReadiness();
})();
