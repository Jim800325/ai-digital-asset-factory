# v0.3 Release Review Package / Artifact Inspection Layer

## Purpose

This layer turns an `ARTIFACT_READY` sandbox result into a deterministic,
human-readable review package. It improves reviewability only.

It does **not**:

- mark Controlled Live LLM Acceptance as passed,
- approve a release,
- enable deployment,
- push to Git,
- deploy to Vercel or production,
- expose production credentials.

The release candidate remains fail-closed at
`WAITING_LIVE_VALIDATION` until real live validation exists.

## Immutable evidence source

Artifact inspection is based on bytes captured at sandbox completion, not on a
mutable workspace.

For every captured artifact:

1. metadata is stored in `sandbox_artifacts`,
2. bytes are stored in `sandbox_artifact_contents`,
3. SHA-256 and byte length are verified against metadata by PostgreSQL,
4. content snapshots and captured metadata become immutable.

The existing sandbox limits still apply:

- maximum 100 artifact files,
- maximum 10 MiB total artifact bytes.

## Review package contents

Each `release_review_packages` row contains:

- artifact manifest,
- file-level status: ADDED / MODIFIED / REMOVED / UNCHANGED,
- bounded unified text diff for UTF-8 files,
- dependency inventory,
- CycloneDX-style SBOM,
- independent test report,
- deterministic static risk summary,
- source tree SHA-256,
- full review package SHA-256,
- baseline package reference,
- content snapshot completeness flag.

### Diff behavior

The first review package for a proposal has no baseline, so every file is
`ADDED`.

Later packages for the same proposal compare against the most recent earlier
review package.

For UTF-8 text files, the package includes a unified diff. Individual text
patches are capped at 50,000 characters. Binary or oversized files are
represented by hashes and size changes only.

## Dependency and SBOM coverage

The deterministic dependency inspector currently reads:

- Python `requirements*.txt`,
- Python `pyproject.toml` project dependencies,
- Poetry dependencies in `pyproject.toml`,
- Node `package.json` dependencies and dev/peer/optional dependencies.

No package registry or network lookup is performed.

The SBOM is generated only from the immutable captured dependency declarations.

## Risk summary

The risk summary is a static heuristic scan. Current signals include:

- dynamic `eval` / `exec`,
- shell/process execution,
- credential/environment access patterns,
- deployment/push commands,
- network client usage,
- filesystem mutation patterns,
- oversized text files that were not scanned.

Risk results are advisory. They never automatically approve or reject a release.

## Hashes

`source_tree_sha256` covers the sorted artifact path/hash/size/media-type tree.

`package_sha256` covers the deterministic review material including:

- candidate/proposal/run identity,
- baseline identity,
- snapshot completeness,
- manifest and diff,
- dependency inventory and SBOM,
- test report,
- risk summary,
- source tree hash,
- generator version.

This lets a reviewer verify that the full package has not been substituted.

## API

Automatic creation happens when a release candidate is created or refreshed.

Manual/idempotent generation:

```text
POST /v1/release-candidates/{candidate_id}/review-package
```

Read/audit:

```text
GET /v1/release-review-packages
GET /v1/release-candidates/{candidate_id}/review-package
```

## Release boundary

A generated review package does not change release state.

Without Controlled Live LLM Acceptance:

```text
release_status           = WAITING_LIVE_VALIDATION
live_validation_verified = false
deployment_enabled       = false
```

Those invariants remain enforced by the Human Release Gate.
