"use strict";
(() => {
  const byId = (id) => document.getElementById(id);
  const setText = (id, value) => {
    const node = byId(id);
    if (node) node.textContent = value === null || value === undefined ? "—" : String(value);
  };
  const requestJson = async (path, options = {}) => {
    const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
    let body = null;
    try { body = await response.json(); } catch (_) { /* keep status */ }
    if (!response.ok) {
      const error = new Error("HTTP " + response.status);
      error.status = response.status;
      throw error;
    }
    return body;
  };
  const shortId = (value) => String(value || "—").slice(0, 16);
  const setRunMessage = (message, state = "") => {
    const node = byId("manualRunMessage");
    if (!node) return;
    node.className = "manual-run-status" + (state ? " " + state : "");
    node.textContent = message;
  };
  const setReadinessMessage = (message, state = "") => {
    const node = byId("readinessMessage");
    if (!node) return;
    node.className = "manual-run-status" + (state ? " " + state : "");
    node.textContent = message;
  };

  let readinessReady = false;
  let activeJobId = null;
  let pollTimer = null;

  const syncRunButton = () => {
    const button = byId("manualRunButton");
    const confirmed = byId("manualRunConfirmCheck")?.checked === true;
    if (button) button.disabled = !readinessReady || !confirmed || Boolean(activeJobId);
  };

  const checkReadiness = async () => {
    readinessReady = false;
    syncRunButton();
    setText("readinessBadge", "CHECKING");
    try {
      const data = await requestJson("/v1/manual-pipeline/readiness");
      setText("redisState", data?.redis_available ? "READY" : "BLOCKED");
      setText("workerState", (data?.active_worker_count ?? 0) + " ACTIVE");
      setText("gateState", data?.execution_enabled ? "ENABLED" : "DISABLED");
      setText("queueState", data?.queue || "—");
      readinessReady = data?.status === "READY";
      setText("readinessBadge", readinessReady ? "READY" : "BLOCKED");
      if (readinessReady) {
        setReadinessMessage("手動執行已就緒，可以建立新的 Pipeline Run。", "ready");
      } else {
        const blockers = Array.isArray(data?.blockers) ? data.blockers.join(" · ") : "UNKNOWN";
        setReadinessMessage("目前保持封鎖 · " + blockers);
      }
    } catch (_) {
      setText("readinessBadge", "BLOCKED");
      setReadinessMessage("無法確認 readiness；為安全起見保持封鎖。", "failed");
    }
    syncRunButton();
  };

  const stopPolling = () => {
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = null;
  };

  const pollJob = async () => {
    if (!activeJobId) return;
    try {
      const job = await requestJson("/v1/manual-pipeline/jobs/" + encodeURIComponent(activeJobId));
      const status = String(job?.status || "UNKNOWN").toUpperCase();
      setText("jobBadge", status);
      if (status === "FINISHED") {
        stopPolling();
        const result = job?.result || {};
        setText("runId", shortId(job?.run_id));
        setText("evidenceCount", result.evidence ?? "—");
        setText("opportunityCount", result.opportunities ?? "—");
        setRunMessage(
          "Pipeline 執行完成 · Evidence " + String(result.evidence ?? "—") +
          " · Opportunities " + String(result.opportunities ?? "—"),
          "ready"
        );
        const link = byId("evidenceLink");
        if (link) {
          link.classList.remove("disabled-link");
          link.setAttribute("aria-disabled", "false");
        }
        activeJobId = null;
        syncRunButton();
        await checkReadiness();
        return;
      }
      if (["FAILED","STOPPED","CANCELED"].includes(status)) {
        stopPolling();
        activeJobId = null;
        setRunMessage("Pipeline 執行失敗或已停止；請查看 Worker / Audit 記錄。", "failed");
        syncRunButton();
        await checkReadiness();
        return;
      }
      setRunMessage("Pipeline 正在執行 · " + status, "running");
      pollTimer = window.setTimeout(pollJob, 2000);
    } catch (_) {
      setRunMessage("暫時無法讀取 Job 狀態，將繼續重試。", "running");
      pollTimer = window.setTimeout(pollJob, 3000);
    }
  };

  const createRun = async () => {
    if (!readinessReady || activeJobId) return;
    const keyInput = byId("manualRunKey");
    const key = keyInput?.value || "";
    if (!key) {
      setRunMessage("請先輸入操作金鑰。", "failed");
      return;
    }
    const button = byId("manualRunButton");
    if (button) button.disabled = true;
    setRunMessage("正在建立 Pipeline Run…", "running");
    try {
      const created = await requestJson("/v1/runs", {
        method: "POST",
        headers: { "X-Manual-Run-Key": key }
      });
      if (keyInput) keyInput.value = "";
      activeJobId = created?.job_id || null;
      setText("jobId", shortId(activeJobId));
      setText("jobBadge", String(created?.status || "QUEUED").toUpperCase());
      setRunMessage("Pipeline 已排入佇列。", "running");
      if (activeJobId) pollJob();
    } catch (error) {
      if (keyInput) keyInput.value = "";
      setRunMessage(
        error?.status === 403 ? "操作金鑰不正確。" : "無法建立 Pipeline Run；請重新檢查 readiness。",
        "failed"
      );
    } finally {
      syncRunButton();
    }
  };

  byId("manualRunConfirmCheck")?.addEventListener("change", syncRunButton);
  byId("refreshReadinessButton")?.addEventListener("click", checkReadiness);
  byId("manualRunButton")?.addEventListener("click", createRun);
  checkReadiness();
})();
