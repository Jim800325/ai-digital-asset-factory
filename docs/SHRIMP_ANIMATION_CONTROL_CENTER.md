# Shrimp Animation Control Center v0.1

## Purpose

The Control Center is the read-only homepage for the Shrimp Animation Provider.

Route:

    /

Data endpoint:

    GET /v1/shrimp-animation/control-center/summary

## Surfaces

- Preview / database / migration health
- Shrimp provider and publisher adapter state
- 10-stage animation production pipeline
- recent provider jobs
- Step 8 Human Review / RELEASE_APPROVED summary
- Step 9 Publish Target / Plan state
- Step 10 Controlled Publisher execution state
- exactly-once Upload and Publish write budgets
- Step 10B Bilibili readiness blockers
- recent Bilibili live-acceptance audit states
- links to Human Review, Publishing Authorization, Software Review and API Docs

## Safety

The Control Center is read-only.

It does not expose or accept:

- SESSDATA
- bili_jct
- publish authorization keys
- publisher execution keys
- Bilibili live acceptance keys

It contains no direct Upload, Publish or Delete action. External mutations remain behind Step 9 / Step 10 / Step 10B gates.

Every summary response includes:

    mode = READ_ONLY_CONTROL_CENTER
    secrets_redacted = true

## Preview branch

The dashboard Preview branch has its own non-account Step 9/10/10B keys and switches. Bilibili account Cookie/MID settings remain intentionally unconfigured until the user supplies them directly in Vercel Preview environment variables.
