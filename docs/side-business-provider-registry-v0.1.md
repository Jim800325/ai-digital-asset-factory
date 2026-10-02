# Side-Business Provider Registry v0.1

## Purpose

The Side-Business Provider Registry turns selected open-source projects into continuously re-evaluated building blocks for the AI Digital Asset Factory.

It is not a list of bookmarks. The daily pipeline:

1. Refreshes the 12 seed providers from GitHub.
2. Searches for new public repositories in side-business automation categories.
3. Re-evaluates license, activity, popularity, maintenance pressure, automation fit, and monetization signals.
4. Promotes or downgrades providers.
5. Maintains a provider BUILD_READY queue.
6. Feeds BUILD_READY providers back into the existing opportunity/evidence pipeline.
7. Keeps the existing cross-source Evidence Gate, Research Validation, Build Proposal, Human Approval, Release Review, and Production Execution gates unchanged.

## Seed providers

| Repository | Role | License policy | Commercial fit |
| --- | --- | --- | --- |
| n8n-io/n8n | AUTOMATION | CONDITIONAL | INTERNAL_USE_RECOMMENDED |
| activepieces/activepieces | AUTOMATION | PERMISSIVE | COMMERCIAL_CORE_ALLOWED |
| langgenius/dify | AI_APP_PLATFORM | CONDITIONAL | INTERNAL_OR_SINGLE_TENANT |
| langflow-ai/langflow | AGENT_WORKFLOW | PERMISSIVE | COMMERCIAL_ALLOWED |
| gitroomhq/postiz-app | DISTRIBUTION | COPYLEFT | AGPL_COMPLIANCE_REQUIRED |
| knadh/listmonk | NEWSLETTER | COPYLEFT | AGPL_COMPLIANCE_REQUIRED |
| unclecode/crawl4ai | DATA_COLLECTION | PERMISSIVE | COMMERCIAL_ALLOWED |
| apify/crawlee | DATA_COLLECTION | PERMISSIVE | COMMERCIAL_ALLOWED |
| dgtlmoon/changedetection.io | MONITORING | PERMISSIVE | COMMERCIAL_ALLOWED |
| browser-use/browser-use | BROWSER_AUTOMATION | PERMISSIVE | COMMERCIAL_ALLOWED |
| DIYgod/RSSHub | DATA_COLLECTION | COPYLEFT | AGPL_COMPLIANCE_REQUIRED |
| bytechefhq/bytechef | AUTOMATION | PERMISSIVE | COMMERCIAL_CORE_ALLOWED |

The seed policy is intentionally explicit for repositories whose GitHub SPDX field alone does not fully describe their commercial restrictions.

## Provider scoring

Total score is deterministic and bounded to 0-100.

- License / commercial usability: 30%
- Recent development activity: 20%
- GitHub popularity: 15%
- Maintenance pressure: 10%
- Automation fit: 15%
- Monetization signals: 10%

Default provider BUILD_READY threshold: 75.

A provider is never auto-promoted when:

- the repository is archived;
- the license is classified RESTRICTED;
- the license cannot be classified and remains UNKNOWN;
- recent development activity falls below the automatic promotion floor;
- automation or monetization fit is too weak.

## Provider state machine

RESEARCH -> WATCH -> BUILD_READY

Any state may move to BLOCKED when the repository becomes archived or commercially ineligible.

BUILD_READY is reversible. Every daily cycle re-scores providers. A provider that falls below the gate is removed from the active queue by marking its queue entry STALE.

## Two different BUILD_READY meanings

Provider BUILD_READY means:

> This repository is currently suitable to be considered as a technical/commercial building block.

It does not mean:

> A side-business product has been validated and may be built or deployed.

The provider queue is fed into the existing opportunity discovery system as GitHub evidence. The existing product opportunity must still pass:

- at least 2 independent source domains;
- Evidence Quality >= 65;
- Source Diversity >= 60;
- Signal Strength >= 55;
- opportunity score >= 75;
- buyer / competitor / pricing validation;
- validated willingness-to-pay;
- validated market gap;
- Research Validation completeness >= 75.

Only then may the existing digital_asset_opportunities.build_readiness become BUILD_READY.

## Automatic iteration after provider BUILD_READY

The automatic loop is:

GitHub discovery
-> Provider Registry
-> provider scoring
-> provider BUILD_READY queue
-> opportunity evidence ingestion
-> cross-source aggregation
-> Research Report
-> Research Validation
-> product BUILD_READY
-> Build Proposal generation

If new evidence changes an opportunity, existing report/validation/proposal refresh logic re-runs and may mark previous artifacts stale.

The loop is therefore self-correcting in both directions: promotion and downgrade are supported.

## Human safety boundary

The following remain non-autonomous:

- Build Proposal approval or rejection.
- Production release approval.
- Deployment authorization.
- Human Production Execution Gate.
- Production promotion and rollback.

A provider reaching BUILD_READY does not enable production execution.

## API

- GET /v1/side-business/providers
- GET /v1/side-business/providers?readiness=BUILD_READY
- GET /v1/side-business/build-queue
- GET /v1/side-business/runs
- POST /v1/side-business/refresh

Manual refresh requires X-Approval-Key. Normal refresh happens automatically inside the existing daily scheduler pipeline.

## Configuration

- SIDE_BUSINESS_REGISTRY_ENABLED=true
- SIDE_BUSINESS_DISCOVERY_ENABLED=true
- SIDE_BUSINESS_RESULTS_PER_QUERY=8
- SIDE_BUSINESS_MAX_CANDIDATES_PER_RUN=40
- SIDE_BUSINESS_BUILD_READY_SCORE=75
- GITHUB_TOKEN is strongly recommended for authenticated GitHub API limits.

## v0.2 composition layer

Provider Composition Planner v0.2 is implemented as the next layer after the BUILD_READY queue.

It selects compatible BUILD_READY providers by role, evaluates license compatibility, persists operating-cost estimates and deterministic stack identities, and emits ACTIVE compositions as technical/commercial evidence back into the existing opportunity pipeline.

See `docs/provider-composition-planner-v0.2.md`.

The composition layer does not bypass cross-source validation and does not authorize builds or production execution.
