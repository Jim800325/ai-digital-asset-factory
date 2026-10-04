# Step 10B.6 — Health-Aware Target Router + Pre-Publish Reservation

## Goal

Route a Shrimp Animation job to a healthy sacrificial Bilibili account before
Step 9 Publish Plan creation, reserve that account briefly, and freeze the exact
account / credential slot / target selection into the new Plan.

## Router

Candidates must satisfy all existing Step 10B.5 rules:

- account ACTIVE
- safety mode SACRIFICIAL
- account allowlist valid
- credential slot ACTIVE
- health HEALTHY
- degradation NORMAL
- health evidence fresh
- active Bilibili Publish Target exists
- daily publish quota remains available

Candidates are ranked by selection_priority and health freshness.

## Reservation

A reservation freezes:

- provider_job_id
- account_id / account_key
- credential_slot_id / slot_key
- credential_version
- target_id / target_key
- selection rank
- selection snapshot
- selection SHA-256
- TTL

Default TTL:

    SHRIMP_BILIBILI_RESERVATION_TTL_MINUTES=15

Only one HELD reservation may exist for an account at a time, and only one HELD
reservation may exist for a job at a time.

Expired reservations are fail-closed.

## Concurrency and quota

The router counts both already-published items for the account's local day and
active HELD reservations against daily_publish_limit.

If the first healthy account is already reserved, the router tries the next
healthy candidate.

## Rebind

Rebind is allowed only while the reservation is HELD and before it is consumed
by a Publish Plan.

Rebind releases the current reservation and routes again while excluding the
previous account.

A consumed reservation cannot be rebound.

Failover never mutates an already-created or already-authorized Publish Plan.
A different account requires a new reservation, a new Step 9 Plan, and a new
human Step 9 authorization.

## Publish Plan binding

A routed Plan freezes:

- reservation_id
- reservation_sha256
- failover_selection_sha256
- account_profile_id / account_profile_sha256
- credential_slot_id / credential_slot_sha256
- credential_version
- target snapshot

The reservation is atomically changed from HELD to CONSUMED in the same
transaction that inserts the Plan.

Step 9 authorization re-verifies that the reservation:

- is CONSUMED
- was consumed by this exact Plan
- has the same selection SHA-256
- still refers to the same account
- still refers to the same credential slot
- still refers to the same target

## APIs

- POST /v1/shrimp-animation/jobs/{job_id}/bilibili-reservations
- GET /v1/shrimp-animation/bilibili-reservations
- GET /v1/shrimp-animation/bilibili-reservations/{reservation_id}
- POST /v1/shrimp-animation/bilibili-reservations/{reservation_id}/release
- POST /v1/shrimp-animation/bilibili-reservations/{reservation_id}/rebind
- POST /v1/shrimp-animation/jobs/{job_id}/routed-publish-plans

Reservation mutations and routed Plan creation require the existing independent
Step 9 publish authorization key. They do not authorize publishing by
themselves.

## Safety

Routing has no Bilibili write side effect.

Reservation has no Bilibili write side effect.

Routed Plan creation remains PENDING_AUTHORIZATION and keeps execution disabled.

Upload / Publish remains behind the existing Step 9, Step 10 and Step 10B
authorization/execution gates.
