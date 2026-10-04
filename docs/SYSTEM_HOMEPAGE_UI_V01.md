# System Homepage UI v0.1

## Reference architecture

The homepage and control-center shell adapt the layout conventions of the
open-source Tabler project (MIT): vertical navigation, page header, compact
status cards, responsive card grids and operational tables.

No external CDN is required. All HTML, CSS and JavaScript are served from the
existing FastAPI static asset mount so the current Content-Security-Policy can
remain `default-src 'self'`.

Reference:
- GitHub: tabler/tabler
- License: MIT
- Adaptation target: FastAPI + static HTML/CSS/JS

## Information architecture

- `/` — frontend system homepage
  - system health
  - factory modules
  - Shrimp pipeline
  - Side-Business registry / BUILD_READY queue
  - ranked opportunity intelligence
  - governance model
- `/admin` — backend control center
- `/control-center` — backend alias
- Existing module routes remain unchanged.

## Design rules

1. Frontend is read-only and explains the system.
2. Backend is operational and compact.
3. Human-gated actions remain in their existing dedicated workspaces.
4. Credentials, cookies, keys and other secrets are never rendered.
5. Mobile navigation collapses without changing workflow semantics.
6. No mojibake: UTF-8 HTML and Chinese-capable system font stacks.
7. Existing business and publishing logic is unchanged.

## Acceptance

- frontend route renders
- backend route and alias render
- static assets are local
- CSP and no-store headers remain
- JavaScript passes `node --check`
- complete pytest remains green
- existing Shrimp Control Center API remains the system status source
