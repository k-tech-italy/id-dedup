# Architecture (id_dedup)

> **Scope:** This document covers the shared architectural constraints and conventions across both apps. For the full dedup wizard technical walkthrough, see [`dedup-flow.md`](dedup-flow.md).

## Two-app architecture

- **`id_dedup.dedup`** — the original 4-step session-based wizard, kept intact for demos. All identity assignment is intentional and manual; the pipeline never auto-assigns.
- **`id_dedup.workflow`** — ticket-based async design (in progress). Cluster review tickets with persistent DB state. Ticket list/detail/review are implemented; upload→clustering wiring and auto-adjudication are not.

The two apps are independent: they share no code, and each has its own `Identity`/`Image` models.

---

## Workflow app (current state)

The workflow app persists state in the DB and tracks work via tickets and conversations, rather than the session-scoped wizard flow.

### Models (`workflow/models.py`)

- **`Batch`** — groups image uploads.
- **`Conversation`** — lifecycle/audit log for a batch. `trigger` is a `Trigger` enum (`upload`, `cluster review`, `adjudication`); queryset filters `pending()` / `completed()` / `errored()`. A `CLUSTER_REVIEW` conversation stores the review outcome in `summary`, including `kept_image_ids`.
- **`Identity`** — UUID, `centroid` VectorField 512-d (null until images assigned) with an HNSW cosine index, timestamps. `update_centroid()` recomputes from assigned image embeddings.
- **`Image`** — UUID, nullable FKs to `Identity`, `ClusterReviewTicket`, and `Batch`, `embedding` VectorField 512-d (null until a task fills it), `source_image`. HNSW cosine index on `embedding`. A `post_delete` signal refreshes the owning identity's centroid.
- **`ClusterReviewTicket`** — UUID, FK→`Batch`, `cluster_label`, `reviewed_by`, `closed_at`. Querysets `.open()` / `.closed()`; `.close(user)` uses an atomic conditional `UPDATE` (DB-level `closed_at IS NULL` guard) so concurrent closers never clobber each other. `is_closed` property.

### Service (`workflow/service.py`)

- `create_tickets_from_result(result, batch)` — creates one ticket per DBSCAN group (label ≥ 0) and persists its images with their embeddings. Singletons (label −1) bypass review. Wrapped in `@transaction.atomic`.
- `get_kept_image_ids(ticket)` — reads `kept_image_ids` from the ticket's `CLUSTER_REVIEW` conversation summary; empty set while the ticket is open.
- `submit_ticket_review(ticket, user, kept_ids)` — closes the ticket (recording the reviewer), writes a `CLUSTER_REVIEW` conversation (audit trail incl. kept/discarded counts), and dispatches `process_reviewed_set` via `.delay()`. Raises `ValueError` if the ticket is already closed.

### Tasks (`workflow/tasks.py`)

`process_reviewed_set` is a Celery stub — a placeholder for auto-adjudication of a reviewed cluster's kept survivors (pgvector matching, identity assignment, adjudication tickets, conversation events). Not implemented.

### Views (`workflow/views.py`, `workflow/urls.py`)

Mounted at `/workflow/` (`app_name="workflow"`), all `@login_required`:

- `tickets/` → `ticket_list` — open/closed tickets via `?status=` filter.
- `tickets/<uuid:pk>/` → `ticket_detail` — shows the cluster's images; disabled form + closed badge once reviewed.
- `tickets/<uuid:pk>/submit/` → `submit_review` — POSTs `keep` IDs and calls `submit_ticket_review`.

Templates: `workflow/ticket_list.html`, `workflow/ticket_detail.html`. Seed for manual testing: `./manage.py seed_tickets`.

### DB writes (workflow app)

The workflow app has its own write paths: `create_tickets_from_result` and `submit_ticket_review` both write inside `@transaction.atomic`. These are separate from — and do not reuse — the dedup app's write path.

---

## Two-stage deduplication pipeline (dedup app)

1. **Stage 1** (`process_images` in `ml/pipeline.py`): extract 512-d ArcFace embeddings → L2-normalise → DBSCAN with cosine metric → `ClusterResult`. No DB access.
2. **Stage 2** (`propose_matches` in `dedup/service/proposals.py`): compute centroid per cluster → pgvector `CosineDistance` query against `Identity.centroid` → `similarity_band` filter → `ClusterProposal` list. Read-only DB.

See [`dedup-flow.md`](dedup-flow.md) for the full technical walkthrough of both stages, the orchestration layer, serialization, and session state.

---

## Architecture boundary rule (dedup app)

> **`pipeline.py` has zero Django imports. `service/proposals.py` owns all ORM interaction for identity matching.**

- Never import Django models, settings, or ORM in `pipeline.py`.
- Never do sklearn/heavy-ML computation in `service/proposals.py`. Lightweight NumPy operations (e.g. `np.stack` for centroid computation) are acceptable.
- `service/workflow.py` imports both `pipeline` and `models` — it is the orchestration layer that bridges ML results to DB persistence.

This boundary is intentional: it allows the ML pipeline to be tested without a Django/DB setup. Violating it breaks that separation.

---

## DB writes: exactly one place (dedup app)

Only `workflow.persist_assignments()` in `service/workflow.py` creates `Identity` and `Image` records (within `@transaction.atomic`). It is called from the adjudication step views (`assign`, `new_identity`, `adjudication_next`) when the last proposal is processed — not from `complete()`. `complete()` is a read-only view that renders the persistence summary already stored in the session. `pipeline.py` and `service/proposals.py` are read-only with respect to the database.

This rule is **scoped to the dedup app**. The workflow app has its own write paths (see the workflow section above).

---

## Conditional UPDATE pattern for idempotent state transitions

Both `ClusterReviewTicket.close()` and `Conversation.close()` use the same pattern: a conditional `UPDATE … WHERE <timestamp> IS NULL` instead of a `self.<field> = …; self.save()`. This exists for two reasons:

1. **Atomicity.** A naive `self.save()` is two steps — read the stale in-memory object, then write it back. Two concurrent callers can both read the pre-close state and both succeed. The conditional UPDATE collapses check-and-write into a single DB statement; exactly one caller's WHERE clause matches.

2. **No clobbering.** `self.save(update_fields=[…])` writes every field touched in the transaction, not just the one being set. The conditional UPDATE only touches the timestamp (and optionally `reviewed_by`), so concurrent changes to `summary` or other fields are never overwritten.

The return value (`updated == 1`) lets callers distinguish "I closed it" from "it was already closed", enabling idempotent retry and early-out logic in the service layer.

This pattern is safe inside or outside a transaction boundary. The `@transaction.atomic` on the service layer guarantees the surrounding operation (e.g. `remove_from_pending` + `is_drained` + `close`) commits as a unit, but does not prevent two separate callers from racing on `close()` itself.

---

## Hard constraint: no auto-assignment (dedup app)

Nothing in `pipeline.py`, `service/proposals.py`, `service/workflow.py`, or `dedup/views.py` ever automatically assigns a cluster to an identity. Every `identity_id` in `wizard_assignments` originates from an explicit user POST to `assign` or `new_identity`. Do not change this.

The workflow app is different by design: auto-adjudication of kept survivors is *planned* in `process_reviewed_set`, but that task is still a stub and assigns nothing today.

---

## Code conventions

- **Ruff**: line-length 120, target py313, broad lint set (see `ruff.toml`). Double quotes, space indent.
- Use `from __future__ import annotations` only where needed (e.g. forward references that can't be resolved at definition time).
- Docstrings should be as descriptive as needed. Inline comments explain *why*, not *what*; multi-line comments are fine when a single line can't capture a design decision.
- No Django Forms, no DRF. Prefer function-based views; class-based views are acceptable when they meaningfully reduce complexity (e.g. dispatching multiple HTTP verbs).
- **Model mutations live on the model.** State-changing operations are model methods (e.g. `Identity.update_centroid()`, `ClusterReviewTicket.close()`), not ad-hoc ORM calls in views or services.
- **Queries live on querysets when convenient.** Reusable filters become `QuerySet` methods (e.g. `ClusterReviewTicket.objects.open()`/`.closed()`, `Conversation.objects.pending()`).
- Session keys are always prefixed `wizard_` (constant `SESSION_PREFIX = "wizard_"` in `views.py`).

See [`testing.md`](testing.md) for test conventions and [`frontend.md`](frontend.md) for frontend patterns.
