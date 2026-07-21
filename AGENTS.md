# id-dedup — Agent Guide

4-step wizard web app that clusters uploaded face images and proposes matches against a database of known identities. All identity assignment is intentional and manual — the pipeline never auto-assigns.

## Stack

| Layer | Technology |
|---|---|
| Backend | Django 5.2, PostgreSQL, pgvector (512-d HNSW cosine index) |
| ML | InsightFace (ArcFace embeddings), scikit-learn (DBSCAN), OpenCV, NumPy |
| Frontend | HTMX 2, Alpine.js 3, SortableJS, Tailwind CSS v4 |
| Build | Webpack (JS bundle), uv (Python deps), npm (JS deps) |
| Python | 3.13+, managed with `uv` |

## Source layout

| Path | Responsibility |
|---|---|
| `src/id_dedup/dedup/pipeline.py` | Stage 1 — Django-free. `ClusterMember`, `ClusterResult`, embedding extraction, DBSCAN clustering, `normalised_mean()` |
| `src/id_dedup/dedup/service/proposals.py` | Stage 2 — ORM + pgvector. `IdentityMatch`, `ClusterProposal`, identity matching against `Identity.centroid` |
| `src/id_dedup/dedup/service/workflow.py` | Orchestration — uploads, file validation, split delegation, assignment CRUD, search, DB persistence |
| `src/id_dedup/dedup/serializers.py` | Session serialization/deserialization (base64 for NumPy arrays) for pipeline and service data structures |
| `src/id_dedup/dedup/models.py` | `Identity` (UUID, display_name, centroid VectorField, image_count) and `Image` (UUID, 512-d VectorField, nullable FK, source_image) |
| `src/id_dedup/dedup/views.py` | 4-step wizard views, session helpers, HTMX partials |
| `src/id_dedup/dedup/urls.py` | URL routes, `app_name="wizard"` |
| `src/id_dedup/config/settings.py` | Django settings; `DATABASE_URL`, `SECRET_KEY`, `DEBUG` from env |
| `tests/conftest.py` | Session-scoped pipeline fixture, `splittable_result`, per-image/person parametrised fixtures; real example images at `tests/examples/` |
| `tests/unit/conftest.py` | Synthetic unit fixtures (`unit_member`, `two_member_group`, `cluster_result_with_groups`, etc.) and `logged_in_client` |
| `tests/unit/helpers.py` | `chainable_qs`, `mock_identity_row`, `unit_vector` |

## Architecture boundary rule

> **`pipeline.py` has zero Django imports. `service/proposals.py` owns all ORM interaction for identity matching.**

- Never import Django models, settings, or ORM in `pipeline.py`.
- Never do sklearn/heavy-ML computation in `service/proposals.py`. Lightweight NumPy operations (e.g. `np.stack` for centroid computation) are acceptable.
- `service/workflow.py` imports both `pipeline` and `models` — it is the orchestration layer that bridges ML results to DB persistence.

This boundary is intentional: it allows the ML pipeline to be tested without a Django/DB setup. Violating it breaks that separation.

## DB writes: exactly one place

Only `workflow.persist_assignments()` in `service/workflow.py` creates `Identity` and `Image` records (within `@transaction.atomic`). It is called from the adjudication step views (`assign`, `new_identity`, `adjudication_next`) when the last proposal is processed — not from `complete()`. `complete()` is a read-only view that renders the persistence summary already stored in the session. `pipeline.py` and `service/proposals.py` are read-only with respect to the database.

## Running the project

```
make develop            # npm install + uv venv + uv sync + direnv allow
./manage.py runserver   # requires DATABASE_URL set, pgvector extension in Postgres
npm run build           # one-shot JS bundle
npm run watch           # watch mode
pytest                  # run tests (plain pytest, not uv run pytest)
```

Copy `.env.example` → `.env` and fill in `DATABASE_URL` and `SECRET_KEY` before running.

## Code conventions

- **Ruff**: line-length 120, target py313, broad lint set (see `ruff.toml`). Double quotes, space indent.
- Use `from __future__ import annotations` only where needed (e.g. forward references that can't be resolved at definition time).
- Docstrings should be as descriptive as needed. Inline comments explain *why*, not *what*; multi-line comments are fine when a single line can't capture a design decision.
- No Django Forms, no DRF. Prefer function-based views; class-based views are acceptable when they meaningfully reduce complexity (e.g. dispatching multiple HTTP verbs).
- Session keys are always prefixed `wizard_` (constant `SESSION_PREFIX = "wizard_"` in `views.py`).

## Two-stage deduplication pipeline

1. **Stage 1** (`process_images` in `pipeline.py`): extract 512-d ArcFace embeddings → L2-normalise → DBSCAN with cosine metric → `ClusterResult`. No DB access.
2. **Stage 2** (`propose_matches` in `service/proposals.py`): compute centroid per cluster → pgvector `CosineDistance` query against `Identity.centroid` → `similarity_band` filter → `ClusterProposal` list. Read-only DB.

See [`.agents/docs/dedup-flow.md`](.agents/docs/dedup-flow.md) for the full technical walkthrough.

## Testing

- Flat functions (`def test_...`) are the default. Class-grouped tests (`class TestFoo`) are acceptable when they improve organisation.
- Shared fixtures go in `conftest.py`. Module-specific fixtures that won't be reused elsewhere may be defined in the test file itself.
- Unit tests in `tests/unit/` must never touch the DB directly; mock the ORM via `chainable_qs`.
- If adding integration tests, they should go in `tests/integration/`

See [`.agents/docs/testing.md`](.agents/docs/testing.md) for patterns, mock recipes, and what to avoid.

## Frontend patterns

- **HTMX**: partial HTML updates. Partial templates are prefixed `_` (e.g. `_cluster_grid.html`, `_matches_list.html`). Primary swap target is `#wizard-content`.
- **Alpine.js**: local reactive state (`x-data`, `x-show`, `x-model`) for the split drawer and new-identity inline form.
- **SortableJS**: drag-drop image reordering between clusters on the review step.
- No client-side routing, no build-time JS framework.
