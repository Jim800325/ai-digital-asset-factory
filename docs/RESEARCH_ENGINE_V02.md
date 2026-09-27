# Research Report Engine v0.2

The Research Report Engine is an OBSERVE-only component. It creates a structured research dossier only for opportunities that have already passed the v0.2 evidence gate and reached CANDIDATE status.

## Required sections

Each report contains:

- Problem
- Buyer
- Existing Alternatives
- Evidence
- Monetization
- Build Complexity
- Risks
- Why Now

The report also stores an evidence snapshot containing the strongest cited evidence rows used when the report was generated.

## Non-hallucination rule

v0.2 is deterministic and does not call an LLM. If a buyer, competitor, alternative, price, or willingness-to-pay signal has not been independently validated, the report explicitly says that it is not yet validated.

## Automatic generation

A report is generated or refreshed when:

1. an opportunity is aggregated;
2. the opportunity status is CANDIDATE;
3. evidence_gate_passed is true.

At the end of each pipeline run, existing qualified candidates are backfilled only when their report is missing or the opportunity has changed since the report was last updated.

## Safety boundary

The engine may read, analyze, score, store, and report.

It does not:

- spend money;
- register accounts;
- send email;
- post to social networks;
- publish websites;
- deploy products;
- push autonomous code;
- modify production servers;
- create paid API resources.

observe_only=true is persisted on every research report.

## API

- GET /v1/research-reports
- GET /v1/research-reports/{opportunity_id}
- GET /v1/opportunities/{opportunity_id}/evidence
