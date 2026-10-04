# Step 10B.11 — Incident Acknowledgement + On-Call Routing + Recovery SLA + Post-Incident Review

## Goal

Extend Publisher Incident handling from detection/recovery into an operational
responsibility lifecycle without adding any new publish or Circuit recovery
authority.

Operational responsibility:

    OPEN
      -> ACKNOWLEDGED
      -> OWNER ASSIGNED
      -> SLA TRACKED
      -> RESOLVED
      -> PIR REQUIRED
      -> PIR COMPLETED
      -> CORRECTIVE ACTIONS CLOSED

Recovery authority remains exclusively in Step 10B.10.

## Incident Ops Key

New independent secret:

    SHRIMP_BILIBILI_INCIDENT_OPS_KEY

It is used only for:

- Incident acknowledgement
- Owner assignment
- On-Call Route changes
- PIR completion
- Corrective Action creation/completion

It must be independent from:

- Recovery Approval Key
- Step 10B Live Acceptance Key
- Step 9 Publish Authorization Key
- Step 10 Publisher Execution Key

Incident Ops actions cannot close Circuit or authorize publishing.

## Acknowledgement

Each Incident now records:

- acknowledgement_status
- acknowledged_by
- acknowledged_at

States:

    UNACKNOWLEDGED
    ACKNOWLEDGED

ACK is written to the immutable Incident Timeline.

## On-Call Routing

On-Call Route Registry contains:

- severity
- primary owner
- backup owner
- ACTIVE / DISABLED state
- metadata

Default routes are created automatically from:

    SHRIMP_BILIBILI_INCIDENT_DEFAULT_OWNER
    SHRIMP_BILIBILI_INCIDENT_SECONDARY_OWNER

The default owner values are:

    publisher-oncall
    publisher-backup

Routes can be updated through Publisher Operations using Incident Ops Key.

New Incidents receive an Owner from their severity route.

## SLA

Defaults:

    SHRIMP_BILIBILI_INCIDENT_ACK_SLA_MINUTES=15
    SHRIMP_BILIBILI_INCIDENT_RECOVERY_SLA_MINUTES=120

Each Incident records:

- ack_due_at
- recovery_due_at
- sla_status

SLA states:

- WITHIN_SLA
- ACK_BREACHED
- RECOVERY_BREACHED
- RECOVERED

The existing scheduled Bilibili Recovery Policy also runs the Incident SLA
evaluator.

SLA breach behavior:

- create immutable SLA Event
- append Incident Timeline event
- queue Operations Notification
- keep existing Circuit / Claim / Recovery Gates unchanged

An SLA breach never performs provider writes and never auto-closes a Circuit.

## Post-Incident Review

Resolved CRITICAL Incidents automatically become:

    PIR REQUIRED

The system creates one Post-Incident Review record.

PIR captures:

- root cause
- contributing factors
- customer impact
- detection gap
- recovery notes
- lessons learned
- review SHA-256
- reviewer / completion time

Current Control Center completion workflow requires:

- Root Cause
- Lessons Learned
- Incident Ops Key

Completing PIR changes Incident:

    pir_status -> COMPLETED

PIR completion does not alter Circuit or Publish authorization.

## Corrective Actions

Each PIR can contain multiple Corrective Actions:

- action key
- description
- owner
- due date
- status
- completion evidence
- completion time

States:

- OPEN
- IN_PROGRESS
- COMPLETED
- CANCELLED

Completion writes an immutable Incident Timeline event.

## Publisher Operations UI

The existing workspace:

    /animation/operations

now includes:

- ACK status
- Incident Owner
- SLA state
- Recovery deadline
- PIR state
- On-Call Route Registry
- SLA Events
- Post-Incident Reviews
- Corrective Actions

Human operations:

- ACK Incident
- assign / change Owner
- edit On-Call Route
- complete PIR
- add Corrective Action
- complete Corrective Action

These actions use Incident Ops Key only.

## APIs

Read:

- GET /v1/shrimp-animation/bilibili-incident-ops-summary
- GET /v1/shrimp-animation/bilibili-oncall-routes
- GET /v1/shrimp-animation/bilibili-incident-sla-events
- GET /v1/shrimp-animation/bilibili-post-incident-reviews
- GET /v1/shrimp-animation/bilibili-corrective-actions

Operations:

- POST /v1/shrimp-animation/bilibili-incidents/{incident_id}/acknowledge
- POST /v1/shrimp-animation/bilibili-incidents/{incident_id}/owner
- POST /v1/shrimp-animation/bilibili-oncall-routes
- POST /v1/shrimp-animation/bilibili-incidents/{incident_id}/pir/complete
- POST /v1/shrimp-animation/bilibili-incidents/{incident_id}/corrective-actions
- POST /v1/shrimp-animation/bilibili-corrective-actions/{action_id}/complete

## Safety invariants

- ACK does not close Circuit.
- Owner assignment does not authorize recovery.
- SLA breach does not retry provider writes.
- PIR completion does not authorize publishing.
- Corrective Actions cannot modify Claim / Reservation / Circuit.
- Recovery still requires Step 10B.10 Recovery Approval + Live Evidence Apply.
- Step 9 / Step 10 / Step 10B publish gates remain mandatory.
