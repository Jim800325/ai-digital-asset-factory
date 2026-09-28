# OpenHands Sandbox Adapter v0.3

This phase gives OpenHands limited code-building permission only inside the already-approved AI Digital Asset Factory sandbox boundary.

## Version pin

The adapter pins OpenHands CLI 1.16.0 in a dedicated container image.

The OpenHands container does not receive production credentials, the host Docker socket, or general Internet access.

## Runtime architecture

OpenHands uses process runtime only inside the hardened outer Docker container.

This does not permit OpenHands process mode on the host.

The outer container enforces:

- read-only root filesystem;
- isolated read/write /workspace mount;
- all Linux capabilities dropped;
- no-new-privileges;
- bounded CPU, memory, and PID count;
- no host Docker socket;
- no production filesystem mounts.

## Network architecture

The OpenHands container joins a per-request Docker network created with --internal.

It cannot reach the public Internet directly.

Its only intended peer is an LLM Gateway named llm-gateway.

OpenHands receives:

- a random per-run local gateway token;
- a model name;
- a base URL pointing at the internal gateway.

It does not receive the upstream provider API key.

### MOCK mode

Used for CI acceptance.

The gateway returns a deterministic OpenAI-compatible trajectory that calls the real OpenHands terminal tool and creates a small artifact and test suite.

No external model API is contacted.

### PROXY mode

Intended for later controlled operation.

The gateway is dual-homed:

- internal network toward OpenHands;
- bridge/egress network toward the configured HTTPS LLM upstream.

Only the gateway receives the upstream API key.

The OpenHands container remains on the internal network only.

## Execution flow

APPROVED build proposal
→ sandbox request
→ policy check
→ per-request workspace
→ per-request internal Docker network
→ LLM gateway
→ real OpenHands CLI headless run
→ artifact capture
→ separate network-none test container
→ ARTIFACT_READY

The build proposal field execution_enabled remains false throughout this flow.

## Headless confirmation boundary

OpenHands headless mode auto-approves its own tool actions.

Therefore all human approval happens before OpenHands is invoked.

OpenHands cannot bypass:

- BUILD_READY;
- current Research Validation;
- current Proposal source fingerprint;
- APPROVED proposal state;
- Sandbox Execution Policy.

## Artifact and test boundary

OpenHands may write only within /workspace.

Only /workspace/artifact is captured as a build artifact.

Tests are run after OpenHands exits in a separate container with network none.

The artifact collector continues to reject symlinks, path escape, excessive file count, and excessive total size.

## Production actions remain unavailable

This phase does not expose:

- Git push;
- Vercel deployment;
- production deployment;
- account registration;
- paid resource creation;
- email or social posting;
- production server modification.

## Configuration

Defaults remain fail-closed:

- SANDBOX_EXECUTION_ENABLED=false
- OPENHANDS_ENABLED=false
- OPENHANDS_GATEWAY_MODE=PROXY
- OPENHANDS_RUNTIME=process

PROXY mode additionally requires:

- OPENHANDS_MODEL
- OPENHANDS_LLM_UPSTREAM_URL using HTTPS
- OPENHANDS_LLM_API_KEY

## Audit API

- GET /v1/openhands-executions
- GET /v1/openhands-executions/{request_id}
- GET /v1/sandbox-runs/{run_id}/artifacts
- GET /v1/sandbox-runs/{run_id}/tests

There is still no public API that accepts an arbitrary shell command or automatically executes an approved proposal.
