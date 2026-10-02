# Provider Composition Planner v0.2

## Purpose

Provider Composition Planner turns individual Provider BUILD_READY repositories into deterministic, auditable candidate stacks.

It does not authorize a product build. Each composition is emitted back into the normal opportunity/evidence system as technical/commercial evidence. Independent market evidence is still required before Product BUILD_READY.

## Flow

```text
Provider BUILD_READY Queue
  -> role buckets
  -> template compatibility
  -> license compatibility
  -> commercial constraints
  -> operating-cost estimate
  -> deterministic stack score
  -> ACTIVE / WATCH / BLOCKED
  -> composition evidence item
  -> Opportunity
  -> Cross-Source Evidence Gate
  -> Research Report
  -> Research Validation
  -> Product BUILD_READY
```

## Templates

v0.2 starts with three deterministic templates:

- `MARKET_INTELLIGENCE_AUTOMATION`: collection + intelligence + automation + optional distribution.
- `AGENTIC_RESEARCH_FACTORY`: collection/browser + intelligence + automation + optional distribution.
- `MONITORING_ALERTS_SERVICE`: monitoring + automation + distribution + optional collection.

## License compatibility

- `PASS`: all members are permissive or weak-copyleft under the current policy.
- `REVIEW`: at least one member is copyleft or conditional; exact commercial constraints are persisted.
- `BLOCKED`: any member has UNKNOWN or RESTRICTED licensing.

## Scoring

```text
75% average provider score
15% weakest provider score
10% license compatibility
```

Default ACTIVE threshold: 72.

## Cost model

v0.2 uses a transparent role heuristic instead of pretending to know live infrastructure prices. Each composition stores a low/high monthly USD estimate, its heuristic basis, included infrastructure classes, and exclusions such as paid model tokens and proxy plans.

## Persistence

Migration 030 adds:

- `side_business_composition_runs`
- `side_business_compositions`
- `side_business_composition_members`

Composition identity is SHA-256(template ID + sorted slot/repository membership). Re-running updates the same stack. Previously valid stacks that can no longer be reproduced become `STALE`.

## Opportunity emission

Only ACTIVE compositions emit discovery items.

Different stacks for the same template share the same opportunity title so the existing opportunity aggregation path treats them as technical evidence for one hypothesis rather than creating near-duplicate products.

Composition evidence remains GitHub-domain evidence and cannot alone satisfy the independent-source-domain Evidence Gate.

## API

- `GET /v1/side-business/compositions`
- `GET /v1/side-business/compositions?status=ACTIVE`
- `GET /v1/side-business/composition-runs`
- `POST /v1/side-business/compositions/refresh`

Manual refresh requires `X-Approval-Key`. The normal daily pipeline runs the planner automatically after the Provider Registry refresh.

## Configuration

- `SIDE_BUSINESS_COMPOSITION_ENABLED=true`
- `SIDE_BUSINESS_COMPOSITION_CANDIDATES_PER_SLOT=3`
- `SIDE_BUSINESS_COMPOSITION_MAX_PER_RUN=24`
- `SIDE_BUSINESS_COMPOSITION_READY_SCORE=72`
- `SIDE_BUSINESS_COMPOSITION_EMIT_LIMIT=12`

## Safety boundary

The planner emits hypotheses and evidence only. It does not approve Build Proposals, start production execution, call the controlled Vercel executor, promote/rollback Production, or publish content.
