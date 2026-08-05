# id-dedup — Agent Guide

Two-app architecture:

- **`id_dedup.dedup`** — the original 4-step session-based wizard, kept intact for demos. All identity assignment is intentional and manual; the pipeline never auto-assigns.
- **`id_dedup.workflow`** — ticket-based async design (in progress). Cluster review tickets with persistent DB state. Ticket list/detail/review implemented; upload→clustering wiring and auto-adjudication are not yet implemented.

Start with [`.agents/docs/architecture.md`](.agents/docs/architecture.md) for the pipeline, boundary rules, and DB-write constraints.

## Stack

| Layer | Technology |
|---|---|
| Backend | Django 5.2, PostgreSQL, pgvector (512-d HNSW cosine index), Celery + Redis (async) |
| ML | InsightFace (ArcFace embeddings), scikit-learn (DBSCAN), OpenCV, NumPy |
| Frontend | HTMX 2, Alpine.js 3, SortableJS, Tailwind CSS v4 |
| Build | Webpack (JS bundle), uv (Python deps), npm (JS deps) |
| Python | 3.13+, managed with `uv` |

## Source layout

### dedup app (demo wizard)

| Path | Responsibility |
|---|---|
| `src/id_dedup/ml/pipeline.py` | Stage 1 — Django-free. `ClusterMember`, `ClusterResult`, embedding extraction, DBSCAN clustering, `normalised_mean()` |
| `src/id_dedup/dedup/service/proposals.py` | Stage 2 — ORM + pgvector. `IdentityMatch`, `ClusterProposal`, identity matching against `Identity.centroid` |
| `src/id_dedup/dedup/service/workflow.py` | Orchestration — uploads, file validation, split delegation, assignment CRUD, search, DB persistence |
| `src/id_dedup/dedup/serializers.py` | Session serialization/deserialization (base64 for NumPy arrays) for pipeline and service data structures |
| `src/id_dedup/dedup/models.py` | `Identity` (UUID, display_name, centroid VectorField, image_count) and `Image` (UUID, 512-d VectorField, nullable FK, source_image) |
| `src/id_dedup/dedup/views.py` | 4-step wizard views, session helpers, HTMX partials |
| `src/id_dedup/dedup/urls.py` | URL routes, `app_name="wizard"`, mounted at `/wizard/` |

### workflow app (ticket-based — in progress)

| Path | Responsibility |
|---|---|
| `src/id_dedup/workflow/models.py` | `Batch`, `Conversation` (lifecycle + trigger enum), `Identity` (UUID, centroid VectorField 512-d, HNSW index), `Image` (UUID, nullable FK→Identity/cluster_ticket/batch, embedding 512-d nullable, source_image), `ClusterReviewTicket` (open/closed querysets, `.close()`, `is_closed`) |
| `src/id_dedup/workflow/service.py` | `create_tickets_from_result`, `get_kept_image_ids`, `submit_ticket_review` |
| `src/id_dedup/workflow/tasks.py` | `process_reviewed_set` — Celery task, currently a stub (auto-adjudication planned) |
| `src/id_dedup/workflow/views.py` | `ticket_list`, `ticket_detail`, `submit_review` |
| `src/id_dedup/workflow/urls.py` | `app_name="workflow"`, mounted at `/workflow/` (`tickets/`, `tickets/<uuid:pk>/`, `tickets/<uuid:pk>/submit/`) |
| `src/id_dedup/workflow/management/commands/seed_tickets.py` | Seeds 5 open tickets with dummy images for manual testing |
| `src/id_dedup/workflow/migrations/` | `0001` initial → `0006` (removes `Image.discarded`) |
| `src/id_dedup/workflow/templates/workflow/` | `ticket_list.html`, `ticket_detail.html` |

### Shared

| Path | Responsibility |
|---|---|
| `src/id_dedup/config/settings.py` | Django settings; `DATABASE_URL`, `SECRET_KEY`, `DEBUG` from env; Celery + Redis config |
| `src/id_dedup/celery.py` | Celery app, autodiscovers `tasks.py` |
| `src/id_dedup/typing/request.py` | `AuthenticatedHttpRequest` (typed request with `user`) |
| `tests/conftest.py` | Django test setup, session-scoped pipeline fixture, `splittable_result`, per-image/person parametrised fixtures; real example images at `tests/examples/` |
| `tests/unit/wizard/conftest.py` | Synthetic unit fixtures (`unit_member`, `two_member_group`, `cluster_result_with_groups`, etc.) |
| `tests/unit/wizard/helpers.py` | `chainable_qs`, `mock_identity_row`, `unit_vector` |
| `tests/integration/conftest.py` | `logged_in_client` fixture |
| `tests/integration/wizard/` | Dedup wizard view tests (auth, landing, wizard flow) |
| `tests/integration/workflow/` | Workflow model/service/view tests |

## Essential rules

- **`ml/pipeline.py` has zero Django imports.** `dedup/service/proposals.py` owns all ORM interaction for identity matching and stays free of heavy ML. `service/workflow.py` is the only module that imports both — the bridge.
- **Single DB write path (dedup app):** only `workflow.persist_assignments()` creates `Identity`/`Image` records. `complete()` is read-only. The workflow app has its own write paths (`create_tickets_from_result`, `submit_ticket_review`).
- **No auto-assignment (dedup app):** every identity assignment originates from an explicit user POST. The workflow app *plans* auto-adjudication in `process_reviewed_set` (not yet implemented).
- **No Django Forms, no DRF.** Prefer function-based views.

Full rationale and detail: [`.agents/docs/architecture.md`](.agents/docs/architecture.md).

## Running the project

```
make develop            # npm install + uv venv + uv sync + direnv allow
make worker             # Celery worker (requires REDIS_URL in env)
./manage.py runserver   # requires DATABASE_URL set, pgvector extension in Postgres
npm run build           # one-shot JS bundle
npm run watch           # watch mode
pytest                  # run tests (plain pytest, not uv run pytest); integration tests need live Postgres
```

Copy `.env.example` → `.env` and fill in `DATABASE_URL`, `SECRET_KEY`, and `REDIS_URL` before running.

## Code conventions

- **Ruff**: line-length 120, target py313, broad lint set (see `ruff.toml`). Double quotes, space indent.
- **Model mutations live on the model; reusable queries go in `QuerySet` methods** when convenient. **Creation/factory methods are static methods on the model — never QuerySet methods** (precedent: `ClusterReviewTicket.new`, `Conversation.create_for_cluster_review`).

## Docs

| Doc | Covers |
|---|---|
| [`.agents/docs/architecture.md`](.agents/docs/architecture.md) | Two-stage pipeline, architecture boundary rule, DB-write constraint, workflow app, conventions |
| [`.agents/docs/dedup-flow.md`](.agents/docs/dedup-flow.md) | Full technical walkthrough of the dedup wizard |
| [`.agents/docs/testing.md`](.agents/docs/testing.md) | Test conventions, mock recipes, what to avoid |
| [`.agents/docs/frontend.md`](.agents/docs/frontend.md) | HTMX, Alpine.js, SortableJS patterns |
