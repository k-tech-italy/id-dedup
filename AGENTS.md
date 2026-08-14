# id-dedup — Agent Guide

Two-app architecture:

- **`id_dedup.dedup`** — the original 4-step session-based wizard, kept intact for demos. All identity assignment is intentional and manual; the pipeline never auto-assigns.
- **`id_dedup.workflow`** — ticket-based async design (in progress). Cluster review tickets with persistent DB state. Upload→register→outbox→clustering wiring, ticket list/detail/review, and conversation drain are implemented; auto-adjudication (`auto_adjudicate_set`) is still a stub.

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
| `src/id_dedup/workflow/models.py` | `Batch` (`skipped_files`, `record_skipped_files()`), `Conversation` (trigger enum + lifecycle: `get_or_create_for_upload`, `drain_images`, `is_drained`, `close`, `resume`, `fail`, `mark_clustered`; pending/completed/errored querysets), `Identity` (UUID, centroid VectorField 512-d, HNSW index), `Image` (UUID, nullable FK→Identity/cluster_ticket/batch, embedding 512-d nullable, source_image unique; `register_uploads`, `store_embedding`, `link_to_ticket`, `bulk_store_embeddings`, `bulk_link_to_ticket`), `ClusterReviewTicket` (open/closed querysets, `.close(user)`, `is_closed`), `OutboxMessage` (durable dispatch: `dispatchable()` queryset, `attempts`/`max_attempts`/dead-letter fields, `new()` factory). Embeddings are persisted for all valid images in the clustering commit — `link_to_ticket()` is a graph edge only, `store_embedding()` is the per-row embedding write |
| `src/id_dedup/workflow/service.py` | `register_upload`, `process_batch` (`_acquire_conversation`/`_run_clustering`/`_commit_clustering`), `create_tickets_from_result`, `get_kept_image_ids`, `submit_ticket_review`, `close_conversation_if_drained` |
| `src/id_dedup/workflow/tasks.py` | `process_batch`, `auto_adjudicate_set` (auto-adjudication stub), `dispatch_outbox` (+ `_dispatch_next`); sweep scheduled via Celery beat |
| `src/id_dedup/workflow/views.py` | `UploadView` (upload + register), `ticket_list` (open/closed/all filter + pagination), `ticket_detail`, `submit_review` |
| `src/id_dedup/workflow/urls.py` | `app_name="workflow"`, mounted at `/workflow/` (`upload/`, `tickets/`, `tickets/<uuid:pk>/`, `tickets/<uuid:pk>/submit/`) |
| `src/id_dedup/workflow/admin.py` | Django admin registrations for `Batch`, `Conversation`, `Identity`, `ClusterReviewTicket`, `Image`, `OutboxMessage` (read-only outbox) |
| `src/id_dedup/workflow/management/commands/seed_tickets.py` | Seeds 20 open tickets with dummy images for manual testing |
| `src/id_dedup/workflow/management/commands/dispatch_outbox.py` | Manual/CI outbox recovery — dispatch pending rows, or `--dead` list / `--requeue-dead` dead-lettered rows |
| `src/id_dedup/workflow/migrations/` | `0001` initial → `0009` (outbox, `Batch.skipped_files`, admin plural labels) |
| `src/id_dedup/workflow/templates/workflow/` | `upload.html`, `ticket_list.html`, `ticket_detail.html`, `_pagination.html` |

### Shared

| Path | Responsibility |
|---|---|
| `src/id_dedup/images.py` | Django-free shared image validation: `validate_image()` (magic-byte check for JPEG/PNG/WEBP) and `UnsupportedImageType`; used by both apps |
| `src/id_dedup/config/settings.py` | Django settings; `DATABASE_URL`, `SECRET_KEY`, `DEBUG` from env; Celery + Redis config; outbox knobs `OUTBOX_MAX_ATTEMPTS` (default 5), `OUTBOX_SWEEP_SECONDS` (default 10) |
| `src/id_dedup/celery.py` | Celery app, autodiscovers `tasks.py` |
| `src/id_dedup/typing/request.py` | `AuthenticatedHttpRequest` (typed request with `user`) |
| `tests/conftest.py` | Django test setup, session-scoped pipeline fixture, `splittable_result`, per-image/person parametrised fixtures; real example images at `tests/examples/` |
| `tests/unit/wizard/conftest.py` | Synthetic unit fixtures (`unit_member`, `two_member_group`, `cluster_result_with_groups`, etc.) |
| `tests/unit/wizard/helpers.py` | `chainable_qs`, `mock_identity_row`, `unit_vector` |
| `tests/unit/test_images.py` | `validate_image`/`UnsupportedImageType` unit tests |
| `tests/integration/conftest.py` | `logged_in_client` fixture |
| `tests/integration/config/test_settings.py` | Settings test — outbox beat schedule uses `OUTBOX_SWEEP_SECONDS` |
| `tests/integration/wizard/` | Dedup wizard view tests (auth, landing, wizard flow) |
| `tests/integration/workflow/` | Workflow model/service/view tests |

## Essential rules

- **`ml/pipeline.py` has zero Django imports** (likewise `id_dedup/images.py`). `dedup/service/proposals.py` owns all ORM interaction for identity matching and stays free of heavy ML. `service/workflow.py` is the only module that imports both — the bridge.
- **Single DB write path (dedup app):** only `workflow.persist_assignments()` creates `Identity`/`Image` records. `complete()` is read-only. The workflow app has its own write paths (`register_upload`, `create_tickets_from_result`, `submit_ticket_review`, `close_conversation_if_drained`).
- **Durable outbox (workflow app):** request/service paths never touch the Celery broker. Async dispatches go through `OutboxMessage` rows written in the same transaction as the business write, then reaped by `dispatch_outbox` (Celery beat sweep or management command).
- **No auto-assignment (dedup app):** every identity assignment originates from an explicit user POST. The workflow app *plans* auto-adjudication in `auto_adjudicate_set` (not yet implemented).
- **No Django Forms, no DRF.** Prefer function-based views.

Full rationale and detail: [`.agents/docs/architecture.md`](.agents/docs/architecture.md).

## Running the project

```
make develop            # npm install + uv venv + uv sync + direnv allow
make worker             # Celery worker (requires REDIS_URL in env)
make dev                # Celery worker + beat scheduler (outbox reaper) in one terminal
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
