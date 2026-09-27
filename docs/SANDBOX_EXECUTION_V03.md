# Sandbox Execution Gate v0.3

This layer separates an APPROVED build proposal from any code execution.

The system remains fail-closed: no proposal is executed automatically, and OpenHands remains disabled until the sandbox layer itself has passed acceptance.

## State machine

APPROVED
→ SANDBOX_BUILD_REQUEST
→ POLICY_PASSED
→ RUNNING
→ ARTIFACT_READY

Failure states:

- BLOCKED
- FAILED
- STALE

No production deployment or release state exists in this layer.

## Execution policy

Every request is required to use:

- Docker sandbox execution
- network policy DENY
- Docker network none
- isolated read/write workspace only
- read-only container root filesystem
- all Linux capabilities dropped
- no-new-privileges
- bounded CPU, memory, and PID count
- no production credentials
- no Docker socket mounted into the sandbox
- no deployment
- no external side effects

The production proposal field execution_enabled remains false.

## Workspace isolation

Each request receives a UUID workspace below SANDBOX_WORKSPACE_ROOT.

The runner verifies that the resolved workspace is a direct child of the configured root.

Only that workspace is mounted read/write at /workspace.

The sandbox does not receive the repository root, home directory, production filesystem, or Docker socket.

## Artifact capture

Only files below /workspace/artifact are captured.

The collector:

- rejects symbolic links;
- verifies resolved paths stay below the artifact root;
- limits file count;
- limits total captured bytes;
- records relative path, SHA-256, byte size, media type, and timestamp.

## Test capture

Test execution is recorded separately from build output.

Each result stores:

- fixed test command;
- exit code;
- stdout;
- stderr;
- pass/fail state;
- timestamp.

## OpenHands boundary

OpenHands is intentionally not executed by this migration.

The OPENHANDS executor can pass policy only when:

- SANDBOX_EXECUTION_ENABLED=true;
- OPENHANDS_ENABLED=true;
- OPENHANDS_RUNTIME=docker.

Process/local OpenHands runtime is rejected.

OpenHands headless execution is treated as an external controller that must remain behind this gate. The next adapter phase may connect it only to the already-isolated workspace/sandbox boundary.

## Acceptance adapter

The ACCEPTANCE executor is test-only. It runs a deterministic build fixture inside a Docker container with network none, produces files inside /workspace/artifact, runs tests, and exercises the exact artifact/test capture path.

It does not invoke OpenHands or any model.

## API

Read-only audit endpoints:

- GET /v1/sandbox-requests
- GET /v1/sandbox-requests/{request_id}
- GET /v1/sandbox-runs/{run_id}
- GET /v1/sandbox-runs/{run_id}/artifacts
- GET /v1/sandbox-runs/{run_id}/tests

No public API endpoint creates or executes sandbox requests in this phase.
