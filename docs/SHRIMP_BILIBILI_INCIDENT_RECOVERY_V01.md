# Step 10B.10 — Publisher Incident Timeline + Recovery Approval Gate + Operations Notifications

## Incident lifecycle

CRITICAL Claim Escalation is promoted into a Publisher Incident:

    CRITICAL ESCALATION
      -> INCIDENT OPEN
      -> RECOVERY REVIEW
      -> HUMAN DECISION
      -> APPROVED RECOVERY APPLY
      -> INCIDENT RESOLVED

Only one OPEN / RECOVERY_REVIEW incident may exist per Bilibili account.

## Incident evidence

Each Incident freezes:

- account
- Claim / Execution binding
- Escalation binding
- Circuit binding
- severity
- evidence snapshot
- SHA-256

Timeline entries are append-only and cover:

- Incident opened
- linked escalation
- recovery requested
- recovery approved / rejected
- recovery applied
- Incident resolved

Historical Timeline rows cannot be updated or deleted.

## Recovery Approval Gate

A Recovery Request is created only after a human operator explicitly requests
review from Publisher Operations.

Request creation requires the Step 10B Live Acceptance Key because the request
is grounded in current live/read-back evidence.

Decision requires a new independent secret:

    SHRIMP_BILIBILI_RECOVERY_APPROVAL_KEY

This key must be different from:

- Shrimp Human Review key
- Step 9 Publish Authorization key
- Step 10 Publisher Execution key
- Step 10B Live Acceptance key

The decision can be APPROVE or REJECT.

APPROVE does not close the Circuit.

## Apply phase

Applying an approved recovery requires the Step 10B Live Acceptance Key again.

Before changing Circuit state, the service re-validates:

- Approval status is APPROVED
- Incident is still current
- evidence SHA has not drifted
- Circuit is OPEN / RECOVERY_PENDING
- no active ambiguous Claim remains
- Credential Slot is ACTIVE
- health status is HEALTHY
- degradation status is NORMAL
- MID status is MATCH
- publish permission is ALLOWED

Only then is Circuit changed to CLOSED and the Incident marked RESOLVED.

There is no Force Close endpoint.

## Interaction with Step 10B.9

Step 10B.9 Automatic Recovery Policy cannot auto-close a Circuit while an
OPEN / RECOVERY_REVIEW Incident exists.

That prevents the automatic policy from bypassing the human Recovery Approval
Gate.

Router behavior remains fail-closed while Circuit is OPEN or RECOVERY_PENDING.

## Notifications

Notification Outbox supports:

- CONTROL_CENTER
- WEBHOOK

Configuration:

    SHRIMP_BILIBILI_NOTIFICATION_WEBHOOK_URL
    SHRIMP_BILIBILI_NOTIFICATION_WEBHOOK_TOKEN

If no webhook URL is configured, notifications remain inside the Publisher
Operations Control Center channel.

The scheduled Recovery Policy performs:

1. read-back-only recovery policy
2. CRITICAL Incident synchronization
3. idempotent Incident notification queueing
4. Notification Outbox delivery

Webhook failures do not alter Incident, Claim, Circuit or recovery state.

Notifications contain operational metadata only; credential values and Gate
keys are never included.

## Operations Console

The existing workspace:

    /animation/operations

now includes:

- Publisher Incidents
- Incident Timeline
- Recovery Approvals
- Notification Outbox
- Claim Escalations
- Circuit Breakers
- Recovery Policy Runs
- Circuit Events

Actions:

- Request Recovery
- APPROVE / REJECT Recovery
- Apply Approved Recovery
- view Timeline

## APIs

Read:

- GET /v1/shrimp-animation/bilibili-incidents
- GET /v1/shrimp-animation/bilibili-incidents/{incident_id}/timeline
- GET /v1/shrimp-animation/bilibili-recovery-approvals
- GET /v1/shrimp-animation/bilibili-notifications

Actions:

- POST /v1/shrimp-animation/bilibili-incidents/{incident_id}/recovery-request
- POST /v1/shrimp-animation/bilibili-recovery-approvals/{approval_id}/decision
- POST /v1/shrimp-animation/bilibili-recovery-approvals/{approval_id}/apply

## Safety invariants

- No blind provider write retry.
- No Force Release for ambiguous Claim.
- No automatic Incident recovery.
- APPROVE alone cannot close Circuit.
- Apply requires a second independent live-evidence gate.
- Evidence drift invalidates the recovery path.
- OPEN / RECOVERY_PENDING Circuit still blocks new Reservation.
- Closing Circuit does not authorize publishing; Step 9, Step 10 and Step 10B
  remain mandatory.
