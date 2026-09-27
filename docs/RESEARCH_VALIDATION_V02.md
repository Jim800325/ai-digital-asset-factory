# Research Validation Layer v0.2

The Research Validation Layer is an OBSERVE-only gate between CANDIDATE and BUILD_READY.

BUILD_READY is a research state only. It does not start development, deployment, purchasing, outreach, publishing, account creation, or any other external action.

## Validation dimensions

Each CANDIDATE is independently evaluated for:

- Buyer
- Competitors
- Pricing
- Willingness-to-Pay
- Market Gap

Each validation record also has a lifecycle state:

- CURRENT: reflects the current qualified CANDIDATE evidence set.
- STALE: the opportunity lost CANDIDATE/evidence-gate qualification and must be revalidated if it later returns.

Each dimension has one status:

- VALIDATED: qualifying evidence exists across at least two independent source domains.
- PARTIAL: some qualifying evidence exists, but cross-source validation is incomplete.
- UNKNOWN: no qualifying evidence was found.

Pricing has a stricter rule: generic words such as pricing, price, subscription, or plan can only support PARTIAL. VALIDATED pricing requires strong evidence such as explicit currency amounts or billing-period amounts across at least two independent domains.

## Completeness score

Weights:

- Buyer: 15
- Competitors: 15
- Pricing: 20
- Willingness-to-Pay: 25
- Market Gap: 25

Status multipliers:

- UNKNOWN: 0.0
- PARTIAL: 0.5
- VALIDATED: 1.0

## BUILD_READY gate

An opportunity becomes BUILD_READY only when all of the following are true:

1. Opportunity status is CANDIDATE.
2. Evidence Gate is passed.
3. Research completeness score is at least 75.
4. Buyer is not UNKNOWN.
5. Competitors are not UNKNOWN.
6. Pricing is not UNKNOWN.
7. Willingness-to-Pay is VALIDATED.
8. Market Gap is VALIDATED.

No build action is connected to this state in v0.2.

## Data integrity hardening

The audit preceding this layer also introduced:

- normalized source domains;
- single-source-class diversity no longer passes the diversity threshold;
- Evidence Gate semantics independent from CANDIDATE score;
- per-domain evidence aggregation so repeated evidence from one domain cannot dominate quality;
- preservation of strongest opportunity scores when clusters merge;
- stable opportunity fingerprint aliases across cluster merges;
- first-seen and last-seen evidence timestamps;
- stale Research Report reconciliation;
- accurate created-item telemetry;
- typed UUID API path parameters.

## API

- GET /v1/research-validations
- GET /v1/research-validations/{opportunity_id}
- GET /v1/research-reports
- GET /v1/research-reports/{opportunity_id}
- GET /v1/opportunities/{opportunity_id}/evidence

All research validation records persist observe_only=true.
