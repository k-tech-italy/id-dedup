# Frontend patterns

> **Scope:** The frontend stack and interaction patterns for both apps. The `id_dedup.dedup` wizard uses the HTMX/Alpine/SortableJS stack described below; the `id_dedup.workflow` app uses plain Django templates (full-page renders, no HTMX yet).

## Stack

HTMX 2 (partial HTML updates), Alpine.js 3 (local reactive state), SortableJS (drag-drop reordering), Tailwind CSS v4. No client-side routing, no build-time JS framework. JS is bundled once via Webpack (`npm run build` / `npm run watch`).

## Pages

- `id_dedup/templates/` — `about.html` (anonymous landing), `dashboard.html` (authenticated home), `base.html`, `registration/login.html`.
- `dedup/templates/wizard/` — the 4-step wizard templates.
- `workflow/templates/workflow/` — `ticket_list.html`, `ticket_detail.html` (plain Django template renders; the detail page uses a checkbox form for kept images and a disabled form + closed badge once the ticket is closed).

## HTMX

- Partial templates are prefixed `_` (e.g. `_cluster_grid.html`, `_matches_list.html`).
- Primary swap target is `#wizard-content` (defined in `wizard/base.html`; wizard app only — the workflow app has no HTMX).
- Redirects after POST are signalled with the `HX-Redirect` header.

## Alpine.js

Local reactive state only (`x-data`, `x-show`, `x-model`), used for the split drawer and the new-identity inline form. The workflow ticket-detail page also uses Alpine for local keep-selection state (`x-data="{ keptIds: [] }"`), independent of HTMX.

## SortableJS

Drag-drop image reordering between clusters on the review step. Wizard app only — the workflow app has no SortableJS.
