# Step 10B.5 — Credential Rotation + Scheduled Health Monitor + Failover

## Credential rotation

Each credential slot now carries a monotonically increasing credential_version.
Rotation switches only the environment-variable prefix after the new prefix has
all required server-side values configured. Cookie values are never persisted.

Rotation records:
- previous_version / new_version
- previous_env_prefix / new_env_prefix
- reason
- immutable SHA-256 evidence
- actor / timestamp

Rotation resets the slot to UNKNOWN health and forces a fresh health probe.

## Failure degradation

Each slot tracks consecutive_failures.

- healthy probe: failures -> 0, degradation_status -> NORMAL
- failed probe below threshold: DEGRADED
- failed probe at/above threshold: QUARANTINED

Threshold:
SHRIMP_BILIBILI_HEALTH_FAILURE_THRESHOLD=3

Only NORMAL + HEALTHY + fresh slots are selectable.

## Failover ranking

SACRIFICIAL candidates are ranked by:
1. selection_priority ascending
2. freshest health evidence
3. account_key

REAL accounts are never considered for automatic failover.

## Scheduled health monitor

Vercel Cron calls:

POST /internal/shrimp-animation/bilibili-health-monitor

Schedule:
0 */6 * * *

The endpoint accepts either:
- Authorization: Bearer <CRON_SECRET>
- X-Shrimp-Health-Monitor-Key

Every monitor run is persisted with per-slot results, counts, selected candidate,
and summary SHA-256. Monitoring is read-only against Bilibili and never calls
upload/publish/delete.

## Publish-time preflight recheck

Before Step 10B Live Acceptance starts, a bound credential slot is rechecked if
its last health evidence is older than:

SHRIMP_BILIBILI_PREFLIGHT_RECHECK_MAX_AGE_MINUTES=5

A failed recheck blocks the live acceptance before any provider write.

## Safety

Failover changes candidate selection only. It does not mutate an already
authorized Publish Plan to a different account or credential slot. A different
account still requires a new Step 9 plan/authorization and Step 10 execution.

Secrets remain server-side environment variables and are never returned by the
rotation, monitor, selection, or health APIs.
