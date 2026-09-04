# Models

Each app owns its own `Identity` and `Image`. They are different models with different
tables — do not treat them as one.

!!! note "Hand-written on purpose"

    This page is not generated. Autodoc over `models.py` would make the documentation
    build import Django and therefore need settings and a database; the build is
    deliberately kept free of both. See
    [Development Guide](../index.md#working-on-these-docs).

## Workflow app

`src/id_dedup/workflow/models.py`

### `Batch`

A group of uploaded images.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `created_at` | datetime | |
| `skipped_files` | array of text | Names of files rejected at registration |

`skipped_files` is an audit trail, not a work queue: only valid images ever get rows, so
this is the only record that something was thrown away. Written by
`record_skipped_files()`; rows are created by the `new()` factory.

### `Conversation`

The lifecycle and audit log for a batch. Self-referential — a conversation can have a
parent — so a review can be traced back to the upload that produced it.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `parent` | FK → `Conversation` | Nullable, `SET_NULL` |
| `user` | FK → user | Nullable, `SET_NULL` |
| `trigger` | text choice | `upload`, `cluster review`, `adjudication` |
| `summary` | JSON | Outcome payload — includes `pending_image_ids`, `kept_image_ids` |
| `error_message` | text | Set by `fail()` |
| `created_at` / `ended_at` | datetime | `ended_at` is null while open |

**Querysets:** `pending()`, `completed()`, `errored()`, `upload_for_batch(batch)`.

**Lifecycle:** `get_or_create_for_upload()`, `create_for_cluster_review()`,
`drain_images()` (idempotently removes IDs from `summary["pending_image_ids"]`),
`is_drained`, `close()`, `resume()` (clears a prior failure; raises `NothingToResume` if
there was none), `fail()`, `mark_clustered()`.

`close()` returns whether *this* call closed it — see
[Conditional UPDATE](../architecture.md#conditional-update-for-state-transitions).

### `Identity`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `centroid` | vector(512) | Nullable until images are assigned |
| `created_at` / `updated_at` | datetime | |

HNSW cosine index `workflow_identity_centroid_idx` on `centroid`.
`update_centroid()` recomputes it from the assigned images' embeddings.

Note there is **no `display_name`** here — unlike the wizard's `Identity`.

### `ClusterReviewTicket`

One unit of human review: a cluster someone must confirm.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `batch` | FK → `Batch` | `CASCADE` |
| `cluster_label` | integer | The DBSCAN label |
| `reviewed_by` | FK → user | Nullable, set on close |
| `created_at` / `closed_at` | datetime | `closed_at` null while open |

**Querysets:** `open()`, `closed()`. **Factory:** `new(batch, cluster_label)`.
**Property:** `is_closed`.

`close(user)` is a single conditional `UPDATE` guarded on `closed_at IS NULL`, so
concurrent closers cannot clobber each other; it returns whether this call won.
`submit_ticket_review` raises `TicketAlreadyClosed` when it loses.

### `Image`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `batch` | FK → `Batch` | Nullable, `SET_NULL` |
| `cluster_ticket` | FK → `ClusterReviewTicket` | Nullable, `SET_NULL` |
| `identity` | FK → `Identity` | Nullable, `SET_NULL` — null means unassigned |
| `embedding` | vector(512) | **Nullable** — null when no face was detected |
| `source_image` | file | Unique (`image_source_image_unique`) |
| `created_at` / `updated_at` | datetime | |

HNSW cosine index `workflow_embedding_idx` on `embedding`.

!!! important "Embedding storage and ticket linking are separate"

    `store_embedding()` writes the vector. `link_to_ticket()` adds the ticket edge and
    **never touches `embedding`**.

    Every image with a detectable face gets its embedding stored during the clustering
    commit — singletons included, before any ticket exists. Adjudication consumers run
    after that commit and can rely on the vectors being there.

**Writes:** `register_uploads()` (uniquifies names, commits files to storage, then one
bulk `INSERT`), `store_embedding()`, `link_to_ticket()`, `bulk_store_embeddings()`,
`bulk_link_to_ticket()`.

The bulk paths set `updated_at` explicitly, because `auto_now` never fires for bulk
operations. A `post_delete` signal refreshes the owning identity's centroid.

### `OutboxMessage`

A durable record of an async dispatch. See
[the durable outbox](../architecture.md#the-durable-outbox).

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `task_name` | text | Celery task to send |
| `payload` | JSON | Task kwargs |
| `created_at` | datetime | |
| `dispatched_at` | datetime | Null until sent successfully |
| `attempts` | integer | Every attempt, successful or not |
| `max_attempts` | integer | Defaults to `OUTBOX_MAX_ATTEMPTS`; a check constraint requires ≥ 1 |
| `last_error` | text | |
| `dead_lettered_at` | datetime | Set when `attempts` reaches the cap |

**Queryset:** `dispatchable()` — not dispatched, not dead-lettered, and
`attempts < max_attempts`. **Factory:** `new(task, payload)`.

Read-only in the Django admin.

## Dedup app (wizard)

`src/id_dedup/dedup/models.py`

### `Identity`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `display_name` | text | Human-readable name |
| `centroid` | vector(512) | Nullable until the first image is assigned |
| `image_count` | integer | Denormalised, updated alongside the centroid |
| `created_at` / `updated_at` | datetime | |

HNSW cosine index `identity_centroid_idx`. `update_centroid()` recomputes the
L2-normalised mean of the assigned embeddings and refreshes `image_count`.

### `Image`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `identity` | FK → `Identity` | Nullable, `SET_NULL` — null means unassigned |
| `embedding` | vector(512) | **Not nullable**, unlike the workflow app's |
| `source_image` | file | |
| `created_at` / `updated_at` | datetime | |

HNSW cosine index `embedding_idx`. A `post_delete` signal refreshes the owning
identity's centroid.

Rows are created only by `workflow.persist_assignments()` — see
[one write path](../architecture.md#one-write-path-wizard).
