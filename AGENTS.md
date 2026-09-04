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
| `src/id_dedup/config/settings.py` | Django settings; `DATABASE_URL`, `SECRET_KEY`, `DEBUG` from env; Celery + Redis config; outbox knobs `OUTBOX_MAX_ATTEMPTS` (default 5), `OUTBOX_SWEEP_SECONDS` (default 60) |
| `src/id_dedup/celery.py` | Celery app, autodiscovers `tasks.py` |
| `src/id_dedup/typing/request.py` | `AuthenticatedHttpRequest` (typed request with `user`) |
| `tests/conftest.py` | Django test setup, session-scoped pipeline fixture, `splittable_result`, per-image/person parametrised fixtures; real example images at `tests/examples/` |
| `tests/unit/wizard/conftest.py` | Synthetic unit fixtures (`unit_member`, `two_member_group`, `cluster_result_with_groups`, etc.) |
| `tests/unit/wizard/helpers.py` | `chainable_qs`, `mock_identity_row`, `unit_vector` |
| `tests/unit/test_images.py` | `validate_image`/`UnsupportedImageType` unit tests |
| `tests/integration/conftest.py` | `logged_in_client` fixture |
| `tests/unit/config/test_environment.py` | Environment access proxy (`env`) unit tests |
| `tests/integration/wizard/` | Dedup wizard view tests (auth, landing, wizard flow) |
| `tests/integration/workflow/` | Workflow model/service/view tests |
| `tests/test_docs.py` | Strict `properdocs build`; skipped when the `docs` group is not installed |

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
make docs               # build the documentation site into .build/docs
make docs-serve         # serve the docs with live reload on :8001
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

### Published documentation site (`docs/`)

User-facing docs, built with `properdocs` (**not** `mkdocs`) from `properdocs.yml` at the repo root. Separate audience from `.agents/docs/` — do not merge the two; see the ownership table in [`docs/dev-guide/index.md`](docs/dev-guide/index.md#where-documentation-lives).

**All technical documentation lives under `docs/dev-guide/`.** The top level is product-facing: a marketing landing page and an operator's user guide. Keep that split — a new technical page belongs in the dev guide, not at the top level.

| Path | Covers |
|---|---|
| `docs/index.md` | Marketing landing page — hero, feature grid, how it works, the two front ends |
| `docs/user-guide/wizard.md` | The 4-step wizard from an operator's point of view |
| `docs/user-guide/tickets.md` | The workflow app: upload → clustering → tickets → review |
| `docs/dev-guide/index.md` | Full dev stack, useful commands, how to work on the docs, doc ownership |
| `docs/dev-guide/setup.md` | Clone → deps → `.env` → `migrate` → `runserver`, plus troubleshooting |
| `docs/dev-guide/architecture.md` | Two-stage pipeline, boundary rule, no-auto-assignment, outbox, conditional UPDATE |
| `docs/dev-guide/testing.md` | Unit/integration split, test settings, ORM mocking |
| `docs/dev-guide/frontend.md` | Which app uses which library, template layout, conventions |
| `docs/dev-guide/reference/models.md` | Hand-written field/method reference for both apps' models |
| `docs/dev-guide/reference/settings.md` | Every environment variable, its cast, default and purpose |
| `docs/_theme/img/` | Hand-authored SVG illustrations (hero, pipeline, clustering, apps, outbox, wizard-steps, ticket-review) and `icons/` for the feature grid |
| `docs/specs/` | Requirement documents. Tracked in git, **excluded from the built site** |
| `.github/workflows/docs.yml` | Builds the site on PRs to `develop`, publishes to GitHub Pages on push to `develop`. Installs `--only-group docs` (no application deps) |

Conventions when editing:

- Nav is one `.pages` file per directory (awesome-pages), not a `nav:` block. A new page must be listed in its directory's `.pages` or the strict build rejects it.
- The build is `strict` with `validation.anchors: warn`, so a broken internal link *or* a broken heading anchor fails it. `tests/test_docs.py` runs the same strict build.
- `.gitignore` line 1 is `.*`; `!.github` and `!**/.pages` are the negations that let the workflow and the nav files through. **Any** new dotfile or dot-directory needs its own negation — verify with `git check-ignore -v <path>` (exit 1 means tracked).
- `mkdocstrings` is configured over `src/` but is only to be used against the Django-free modules (`ml/pipeline.py`, `images.py`). Autodoc over `models.py` would make the docs build require Django settings and a database — `docs/dev-guide/reference/models.md` is hand-written for that reason.
- CI installs `uv sync --only-group docs`, which omits the application dependencies. Valid only while `mkdocstrings` has no `:::` directive; adding one means switching the workflow to `--group docs`.
- **SVG gotcha:** a rule in an SVG's own `<style>` block beats a presentation attribute on an element. Do not set `fill`/`stroke` in a shared class if individual elements need to override it — put the shared geometry in the class and the colours on each element.
