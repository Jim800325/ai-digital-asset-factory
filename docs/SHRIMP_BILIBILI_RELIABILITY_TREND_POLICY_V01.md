# Step 10B.13 — Reliability Trend + Error Budget Burn Rate + Regression Detection + Policy Recommendations

## Purpose

Step 10B.13 extends the immutable Step 10B.12 Reliability Scorecard into a
time-series governance layer.

It is read-only / observe-only.

It cannot:

- open or close Circuit Breakers
- release Claims or Reservations
- retry Upload / Publish
- approve recovery
- change SLO targets automatically
- change account limits or publishing exposure
- modify Step 9 / Step 10 / Step 10B gates

## Trend points

Each persisted Reliability Scorecard is projected into:

- DAILY trend point
- WEEKLY trend point

Trend points capture:

- Reliability Score
- ACK success rate
- Recovery success rate
- Ambiguity rate
- Circuit opens
- recurring Root Cause count

Trend history is immutable.

Regression detection compares the most recent two distinct DAILY buckets, so
multiple manual scorecard generations within one day do not create a false
day-over-day regression.

## Error Budget Burn Rate

Default windows:

    SHRIMP_BILIBILI_BURN_SHORT_WINDOW_HOURS=6
    SHRIMP_BILIBILI_BURN_LONG_WINDOW_HOURS=24

Burn rate is deterministic:

    actual failure rate / allowed SLO failure rate

For a 95 percent SLO:

    allowed failure rate = 5 percent

Examples:

    0 failures / 20 events -> burn 0.0
    1 failure  / 20 events -> burn 1.0
    2 failures / 20 events -> burn 2.0

Default thresholds:

    SHRIMP_BILIBILI_BURN_WATCH_THRESHOLD=1.0
    SHRIMP_BILIBILI_BURN_FAST_THRESHOLD=2.0

States:

- HEALTHY
- WATCH
- FAST_BURN
- EXHAUSTED

The current engine emits HEALTHY / WATCH / FAST_BURN from observed rate.
EXHAUSTED is reserved for future explicit budget-exhaustion evidence.

Burn evaluation is calculated independently for:

- ACK short window
- ACK long window
- Recovery short window
- Recovery long window

Global and each ACTIVE Bilibili account receive independent evaluations.

## Regression Detection

Current daily bucket is compared with the previous distinct daily bucket.

Default thresholds:

    SHRIMP_BILIBILI_REGRESSION_SCORE_DROP_POINTS=8.0
    SHRIMP_BILIBILI_REGRESSION_RATE_DROP_PERCENT=10.0
    SHRIMP_BILIBILI_REGRESSION_AMBIGUITY_INCREASE_PERCENT=5.0

Metrics:

- RELIABILITY_SCORE
- ACK_SUCCESS_RATE
- RECOVERY_SUCCESS_RATE
- AMBIGUITY_RATE
- CIRCUIT_OPEN_COUNT
- RECURRENCE_COUNT

Examples:

- Reliability Score drops by 8+ points -> regression
- ACK success rate drops by 10+ percentage points -> regression
- Recovery success rate drops by 10+ percentage points -> regression
- Ambiguity rate rises by 5+ percentage points -> regression
- Circuit opens increase by 1+ -> regression
- recurring Root Causes increase by 1+ -> regression

Severity is deterministic:

- WARNING at threshold
- CRITICAL when the absolute regression reaches at least 2x threshold

Regression evidence stores baseline/current values and exact compared windows.

## Policy Recommendations

Recommendations are generated from fixed deterministic rules.

No LLM decides whether a recommendation exists.

Categories:

- ACK_PROCESS
- RECOVERY_PROCESS
- PROVIDER_AMBIGUITY
- CIRCUIT_STABILITY
- ROOT_CAUSE_RECURRENCE
- ERROR_BUDGET

Examples:

- ACK SLO below target -> review on-call / ACK handoff
- Recovery SLO below target -> review evidence and approval path latency
- Ambiguity above target -> inspect provider read-back identity/evidence quality
- Circuit opens present -> correlate Circuit events with credential/provider failures
- FAST_BURN -> prioritize reliability work before increasing automation exposure
- recurring Root Cause -> verify PIR Corrective Actions address the repeated mechanism
- Regression detected -> compare current/previous windows before changing thresholds

Recommendations contain:

- priority
- category
- title
- recommendation text
- rationale
- evidence snapshot / hashes

There is intentionally no Apply Recommendation API or button.

## Database

Migration:

    054_shrimp_bilibili_reliability_trend_policy.sql

Tables:

    shrimp_bilibili_reliability_trend_points
    shrimp_bilibili_error_budget_burn_evaluations
    shrimp_bilibili_reliability_regressions
    shrimp_bilibili_reliability_policy_recommendations

Trend and Burn history are immutable.

## Control Center

Existing workspace:

    /animation/reliability

now also displays:

- DAILY / WEEKLY trend
- short / long ACK burn rate
- short / long Recovery burn rate
- open regressions
- read-only policy recommendations

The page exposes no execution control for recommendations.

## APIs

Read-only:

- GET /v1/shrimp-animation/bilibili-reliability-trend-dashboard
- GET /v1/shrimp-animation/bilibili-reliability-trends
- GET /v1/shrimp-animation/bilibili-error-budget-burn
- GET /v1/shrimp-animation/bilibili-reliability-regressions
- GET /v1/shrimp-animation/bilibili-reliability-policy-recommendations

Protected generation:

- POST /internal/shrimp-animation/bilibili-reliability-analysis

The protected analysis endpoint uses the existing Health Monitor Key or
CRON_SECRET authorization model.

## Daily workflow

The Step 10B.12 reliability workflow now executes:

1. generate rolling Reliability Scorecards
2. generate DAILY / WEEKLY Trend
3. evaluate short / long Burn Rate
4. detect regressions
5. generate read-only policy recommendations

Any failure causes the workflow to fail rather than silently leaving the
analysis layer stale.

## Safety invariants

- Burn Rate cannot modify SLO targets.
- Regression Detection cannot open Circuit.
- Recommendation generation cannot execute changes.
- Reliability Score cannot authorize publishing.
- No recommendation can bypass Human Recovery Approval.
- Step 9 / Step 10 / Step 10B remain authoritative for publishing.
