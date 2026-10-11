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

  const load = async () => {
    try {
      const [workbench, pipeline] = await Promise.all([
        fetchJson("/v1/workbench-summary"),
        fetchJson("/v1/manual-pipeline/readiness")
      ]);
      const system = workbench?.system || {};
      setText("platformStatus", system.status || "DEGRADED");
      setText("databaseStatus", system.database || "—");
      setText("migrationStatus", system.migrations || "—");
      setText("pipelineStatus", system.pipeline || "—");
      setText("workerCount", system.worker_count ?? "—");
      setText("queueName", pipeline?.queue || "—");
      setText("redisStatus", pipeline?.redis_available ? "AVAILABLE" : "UNAVAILABLE");
      setText("executionGate", pipeline?.execution_enabled ? "ENABLED" : "DISABLED");
      setText("databaseCardStatus", system.database || "—");
      setText("migrationCardStatus", system.migrations || "—");
    } catch (_) {
      setText("platformStatus", "DEGRADED");
      setText("databaseStatus", "UNAVAILABLE");
      setText("migrationStatus", "UNAVAILABLE");
      setText("pipelineStatus", "UNAVAILABLE");
      setText("workerCount", "—");
      setText("queueName", "—");
      setText("redisStatus", "UNAVAILABLE");
      setText("executionGate", "FAIL-CLOSED");
      setText("databaseCardStatus", "UNAVAILABLE");
      setText("migrationCardStatus", "UNAVAILABLE");
    }
  };

  load();
})();