# Frontend

No client-side router, no build-time JS framework. Pages are server-rendered Django
templates; JavaScript is a thin interaction layer bundled once by Webpack.

| Library | Role |
|---|---|
| HTMX 2 | Partial HTML swaps |
| Alpine.js 3 | Local reactive state |
| SortableJS | Drag-and-drop between clusters |
| Tailwind CSS v4 | Styling |

```shell
npm run build               # one-shot bundle
NODE_WATCH=1 npm run build  # rebuild on change
```

## The two apps use different amounts of this

This is the thing to know before editing a template:

| | `dedup` (wizard) | `workflow` (tickets) |
|---|---|---|
| HTMX | Yes — the whole wizard | **No** |
| Alpine | Split drawer, new-identity form | Keep-selection on ticket detail |
| SortableJS | Yes | **No** |
| Otherwise | — | Plain full-page renders; the upload page uses inline vanilla JS for drag-drop |

Do not reach for HTMX in the workflow app because the wizard uses it. The workflow app
is deliberately plain.

## Where templates live

```
src/id_dedup/templates/                 base.html, about.html, dashboard.html,
                                        registration/login.html
src/id_dedup/dedup/templates/wizard/    the 4-step wizard
src/id_dedup/workflow/templates/workflow/  upload, ticket_list, ticket_detail,
                                        _pagination
```

## Conventions

- **Partials are prefixed `_`** — `_cluster_grid.html`, `_matches_list.html`,
  `_pagination.html`.
- **The wizard's swap target is `#wizard-content`**, defined in `wizard/base.html`.
- **Redirect after POST with the `HX-Redirect` header**, not a 302, when HTMX made the
  request.
- **Alpine holds local state only** — `x-data`, `x-show`, `x-model`. Anything that must
  survive a navigation belongs on the server.

## Tailwind

A copy of the Tailwind guide used on this project is kept alongside these docs:
[tailwind-guide.pdf](assets/tailwind-guide.pdf).
