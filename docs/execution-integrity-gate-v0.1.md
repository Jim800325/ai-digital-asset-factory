# Controlled Production Release Executor v0.1 — Execution Integrity Gate

Status: **IMPLEMENTATION STEP 2 — MOCK ONLY / NO PRODUCTION WRITES**

This step sits between the immutable execution snapshot and candidate preparation.

## Gate contract

Immediately before MOCK preparation the gate revalidates:

- immutable execution bundle schema, artifact paths, exact bytes, byte sizes and SHA-256 values
- execution bundle SHA-256 and execution SHA-256
- Deployment Plan / authorization / Release Candidate / Review Package bindings
- current persisted artifact bytes against the frozen execution bundle
- bound Live Acceptance audit ID, evidence SHA-256 and audit chain SHA-256
- source commit, deployment source commit and source Vercel deployment ID
- executor safety flags (MOCK, execution disabled, automatic execution disabled, automatic promotion disabled)

A failed check is persisted and the execution remains in its prior state.

## Append-only audit evolution

The gate intentionally does **not** require the current global manifest root or chain head to equal the values captured when the Deployment Plan was authorized.

Instead:

1. the current registry must still be VERIFIED
2. its manifest root and chain must validate
3. the bound audit/evidence must still match exactly
4. the previously captured global chain head must still be either the current verified head or a verified ancestor entry in the current chain

Therefore a valid append-only extension is accepted, while mutation, removal, re-binding, or a broken chain fails closed.

## Persistence

Migration 024_execution_integrity_gate.sql adds production_release_execution_integrity_checks.

Each check records VERIFIED or BLOCKED, execution SHA-256, blocking reasons, current manifest root and chain head, ancestor/extension evidence, external_side_effects=DENY, and production_traffic_changed=false. Rows are append-only.

## CI acceptance

The integration path proves:

1. an authorized immutable plan creates one execution snapshot
2. mutated bound audit evidence blocks before PREPARING
3. the blocked check is persisted and execution remains SNAPSHOT_CREATED
4. restored evidence plus a verified append-only chain extension passes
5. MOCK preparation reaches READY_FOR_PROMOTION
6. duplicate prepare remains idempotent
7. all Production execution / automatic execution / automatic promotion flags remain false

## Explicitly not implemented

- no Vercel API write adapter
- no Production deployment creation
- no Production promotion
- no Production rollback
- no human PROMOTE decision endpoint
- no background release worker

The next implementation boundary remains the provider PREPARE layer, still against MOCK first and without Production traffic changes.
