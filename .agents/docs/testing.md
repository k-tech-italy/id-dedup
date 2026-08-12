# Testing guide

> **Scope:** Tests cover both apps. `tests/unit/wizard/` holds DB-free unit tests for the dedup pipeline/services/serializers. `tests/integration/` holds DB-backed tests: wizard view tests under `wizard/` and workflow model/service/view tests under `workflow/`.

## Running tests

```
pytest
```

Tests run against a dedicated test settings module (`tests/settings.py`), forced via `--ds tests.settings` in `[tool.pytest.ini_options]` (this beats any exported `DJANGO_SETTINGS_MODULE`). It seeds a stable test `SECRET_KEY` before importing the app's real settings module (`id_dedup.config.settings`), so tests never depend on `.env` for `SECRET_KEY` (or `DEBUG`). `DATABASE_URL` is still read via the `env` proxy from the shell environment/`.env` (direnv) — required for integration tests, falling back to the proxy default if unset. `tests/unit/` mocks the ORM and never touches a real DB; `tests/integration/` runs against the real Postgres + pgvector configured by `DATABASE_URL`.

---

## File layout

```
tests/
  settings.py                test settings module (--ds): seeds SECRET_KEY, imports app settings
  conftest.py               Django settings bootstrap (env-proxy settings module); session-scoped
                            pipeline fixture (cluster_result); splittable_result; per-image/person
                            parametrised fixtures; real images at tests/examples/
  examples/
    person1/ … person4/     real face photos used by pipeline integration fixtures
  unit/
    test_images.py        validate_image / UnsupportedImageType (magic-byte detection, stream rewind)
    wizard/
      conftest.py           synthetic, DB-free unit fixtures (query_centroid, unit_member,
                            two_member_group, cluster_result_with_groups, strong_and_weak_match, close_matches)
      helpers.py            chainable_qs, mock_identity_row, unit_vector
      test_pipeline.py      extract_embedding, process_images, ClusterResult.split
      test_services.py      ClusterProposal, propose_for_members, propose_matches, _query_candidates,
                            process_uploads, apply_split, create_assignment,
                            create_new_identity_assignment, persist_assignments, search_identities
      test_dedup_models.py  Identity.update_centroid, Image.post_delete signal handler
      test_serializers.py   round-trip serialize/deserialize for all data structures
  integration/
    conftest.py             logged_in_client (DB-backed test client fixture, uses model-bakery)
    config/
      test_settings.py      outbox beat schedule uses OUTBOX_SWEEP_SECONDS
    wizard/
      test_views_wizard.py  4-step wizard view tests (session helpers _setup_result, _setup_proposals, _make_identity)
      test_views_landing.py landing page and dashboard tests
      test_views_auth.py    login/logout view tests
    workflow/
      conftest.py           global model-bakery *factory fixtures + derived instance fixtures
                            (batch_factory, image_factory, linked_image_factory, cluster_review_ticket_factory,
                            conversation_factory, user_factory, outbox_message_factory; batch,
                            cluster_review_ticket, closed_cluster_review_ticket)
      test_workflow_models.py      Batch, Conversation, Identity, Image, ClusterReviewTicket, OutboxMessage model behaviour
      test_models_conversation.py  Conversation is_drained/close/drain_images
      test_conversation_drain.py   Conversation resume/fail/mark_clustered
      test_create_tickets.py       create_tickets_from_result
      test_service_get_kept_image_ids.py  get_kept_image_ids
      test_submit_review.py        submit_ticket_review
      test_service_close_drained.py  close_conversation_if_drained
      test_ticket_models.py        ClusterReviewTicket querysets and .close()
      test_ticket_list_view.py     ticket_list view (status filter + pagination)
      test_ticket_detail_view.py   ticket_detail view
      test_upload_view.py          upload view + register_upload
      test_process_batch.py        process_batch + auto_adjudicate_set stub
      test_outbox.py               OutboxMessage + dispatch_outbox + management command
```

---

## Unit test conventions

- **Flat functions** (`def test_...`) are the default.
- **Class-grouped** tests (`class TestFoo`) are acceptable where they improve organisation.
- Shared fixtures go in `conftest.py`. Module-specific fixtures that won't be reused elsewhere may be defined in the test file itself.
- `tests/unit/` tests must never touch the DB directly. Mock the ORM (see below).

---

## Mocking the ORM

`_query_candidates` in `service/proposals.py` is the only real ORM call in the pipeline/services stack. Everything else is pure Python or NumPy.

### Mocking `propose_for_members` / `propose_matches`

Patch `_query_candidates` at the module level to skip the DB entirely:

```python
from unittest.mock import patch
from id_dedup.dedup.service.proposals import propose_for_members

@patch("id_dedup.dedup.service.proposals._query_candidates", return_value=[])
def test_empty_matches(mock_qc, unit_member):
    proposal = propose_for_members([unit_member])
    assert proposal.is_new_identity
```

### Mocking `_query_candidates` itself

Patch both `Identity` and `Image` at the module level and inject a `chainable_qs`. The function queries `Identity.centroid` directly and also fetches image URLs via `Image`:

```python
from unittest.mock import patch, MagicMock
from tests.unit.wizard.helpers import chainable_qs, mock_identity_row
from id_dedup.dedup.service.proposals import _query_candidates

def test_collapses_per_identity(query_centroid):
    rows = [
        mock_identity_row(identity_id=1, display_name="Alice", distance=0.1, image_count=3),
    ]
    with (
        patch("id_dedup.dedup.service.proposals.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.proposals.Image") as MockImage,
    ):
        MockIdentity.objects.filter.return_value = chainable_qs(rows)
        MockImage.objects.filter.return_value = chainable_qs([])
        results = _query_candidates(query_centroid, top_k=5, min_similarity=0.6, similarity_band=0.1)
    assert len(results) == 1
    assert results[0].matched_image_count == 3
```

### Mocking `workflow.py` functions

`workflow.py` functions depend on `pipeline` and Django ORM. Patch at the module level:

```python
from unittest.mock import patch
from id_dedup.dedup.service.workflow import process_uploads

@patch("id_dedup.dedup.service.workflow.pipeline.process_images")
def test_process_uploads_delegates_to_pipeline(mock_process):
    # mock_process.return_value = ClusterResult(...)
    ...
```

For `persist_assignments`, the `@transaction.atomic` decorator wraps the function. Even with every ORM call mocked, the decorator's `__enter__` calls `connection.get_autocommit()` which hits the database. Tests use an `autouse` fixture to swap in the unwrapped original:

```python
@pytest.fixture(autouse=True)
def _noop_transaction_atomic(monkeypatch):
    import id_dedup.dedup.service.workflow as mod
    monkeypatch.setattr(mod, "persist_assignments", mod.persist_assignments.__wrapped__)
```

For `search_identities`, patch `Identity.objects` and provide a `chainable_qs`:

```python
from unittest.mock import patch
from tests.unit.wizard.helpers import chainable_qs
from id_dedup.dedup.service.workflow import search_identities

def test_search_merges_registry():
    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        mock_identity = MagicMock()
        mock_identity.pk = "uuid-1"
        mock_identity.display_name = "Alice"
        MockIdentity.objects.filter.return_value = chainable_qs([mock_identity])
        results = search_identities("Ali", registry={"2": "Alicia"})
    assert len(results) == 2  # Alice from DB + Alicia from registry (both match "Ali")
```

### Helper reference

| Helper | Signature | What it does |
|---|---|---|
| `chainable_qs(rows)` | `list → MagicMock` | Queryset mock that chains `filter/annotate/select_related/order_by` and iterates `rows` |
| `mock_identity_row(identity_id, display_name, distance, image_count=1)` | `→ MagicMock` | Minimal Identity row mock with `.id`, `.display_name`, `.distance`, `.image_count` |
| `unit_vector(seed)` | `int → np.ndarray` | Deterministic L2-normalised 512-d float32 vector |

### Common fixtures (`tests/unit/wizard/conftest.py`)

| Fixture | Description |
|---|---|
| `query_centroid` | `unit_vector(seed=0)` — a fixed centroid for `_query_candidates` tests |
| `unit_member` | Single `ClusterMember` with deterministic embedding |
| `two_member_group` | Two `ClusterMember` objects with different embeddings |
| `cluster_result_with_groups` | `ClusterResult` with two groups and one singleton — used by `propose_matches` tests |
| `strong_and_weak_match` | Two `IdentityMatch` objects with large similarity gap (0.9 vs 0.6) |
| `close_matches` | Two `IdentityMatch` objects with small similarity gap (0.88 vs 0.85) |

The `splittable_result` fixture is defined in the top-level `tests/conftest.py` (function-scoped, synthetic, no real images). Used by `test_pipeline.py` and `test_services.py` for split/apply_split tests. `logged_in_client` lives in `tests/integration/conftest.py` — it hits the DB and is only for integration tests.

### Common mocking patterns

**Dual-patching `Identity` + `Image`:** Many `_query_candidates` tests patch both models simultaneously since the function queries `Identity.centroid` and separately fetches image URLs via `Image`:

```python
with (
    patch("id_dedup.dedup.service.proposals.Identity") as MockIdentity,
    patch("id_dedup.dedup.service.proposals.Image") as MockImage,
):
    MockIdentity.objects.filter.return_value = chainable_qs(rows)
    MockImage.objects.filter.return_value = chainable_qs([])
```

**Filesystem mocking for `persist_assignments`:** Patch `pathlib.Path.exists` to control whether image files are found on disk:

```python
with patch.object(pathlib.Path, "exists", return_value=True):
    summary = persist_assignments(assignments, [proposal], tmpdir_name=None)
```

**Model instantiation without DB:** `test_dedup_models.py` uses `Identity.__new__(Identity)` to create model instances that bypass `__init__` and avoid DB calls, then tests with patched `save()`.

**Django signal testing:** The `Image.post_delete` signal handler is tested by directly sending the signal:

```python
from django.db.models.signals import post_delete
post_delete.send(sender=Image, instance=fake_image, using="default")
```

---

## View tests

Wizard view tests live in `tests/integration/wizard/test_views_wizard.py` and use Django's test client. Session state is set up via helper functions defined at the top of the file (not fixtures):

- `_setup_result(client, result)` — serialises a `ClusterResult` into the session.
- `_setup_proposals(client, proposals, adj_index)` — serialises proposals and sets `adj_index`.

These call functions from `dedup/serializers.py` (`serialize_result`, `serialize_proposal`), not raw JSON.

Module-level factory functions `_result()` and `_proposals(count)` create test data (`ClusterResult` and `list[ClusterProposal]`) without touching the DB. `_make_identity(**kwargs)` builds a DB-backed `dedup.Identity` (`centroid=None`) via model-bakery for the adjudication/search/complete tests.

Wizard view tests that touch the ORM or use session state across requests need `@pytest.mark.django_db(transaction=True)` — this includes `search`, `complete`, and `assign` with a DB-backed identity, plus the auth/landing tests. Tests that only exercise session logic and return HTML fragments generally do not need it.

Workflow view tests (`tests/integration/workflow/test_ticket_list_view.py`, `test_ticket_detail_view.py`) use `@pytest.mark.django_db` and the `logged_in_client` fixture from `tests/integration/conftest.py`. `submit_review` → `submit_ticket_review` enqueues `auto_adjudicate_set` via a durable `OutboxMessage` (no broker call in the request path), so the submit-review tests don't need a reachable Redis broker.

HTMX redirect responses carry an `HX-Redirect` header; the Django test client exposes the final URL as `resp.url`. Check that, not the response body.

---

## What not to do

- Do not put shared/reusable fixtures inside test files — those belong in `conftest.py`.
- Do not call `Model.objects.create()` or `.create_user()` in integration tests — use the model-bakery instance fixtures, a module-local `_make_*` helper, or inline `baker.make(...)`.
- Do not write DB-hitting tests in `tests/unit/`. Mock the ORM; anything needing a real DB belongs in `tests/integration/`.
- Do not use `@pytest.mark.django_db` without `transaction=True` for wizard view tests — session-backed tests need it.

---

## Integration tests

`tests/integration/` requires a live PostgreSQL + pgvector instance (`DATABASE_URL`). No Redis broker is needed: async dispatches are asserted as `OutboxMessage` rows, and the outbox dispatch tests monkeypatch `send_task`. Keep these separate from the DB-free unit tests under `tests/unit/`.

### Model-bakery

All row creation in integration tests goes through `model-bakery` — never `Model.objects.create(...)` or `.create_user(...)`. This keeps fixtures compact and lets overrides ride on `**kwargs`.

#### Global factory fixtures + local instance fixtures

`tests/integration/workflow/conftest.py` follows a **global factories + local fixtures** model. It defines a private `_factory(model, **defaults)` helper that returns a `_make(**kwargs)` callable merging `defaults` with per-call kwargs, then exposes one **factory fixture** per model. Module-local fixtures (or tests) call those factories with their own overrides rather than `baker.make` directly:

| Factory fixture | Defaults | Returns |
|---|---|---|
| `batch_factory` | — | a `_make(**kwargs)` producing a `Batch` |
| `image_factory(batch)` | `batch` | a `_make(**kwargs)` producing an `Image` |
| `linked_image_factory(image_factory, cluster_review_ticket)` | `batch=ticket.batch, cluster_ticket=ticket` | a `_make(tmp_path, name, **kwargs)` writing a real file and producing a ticket-linked `Image` |
| `cluster_review_ticket_factory(batch)` | `cluster_label=0, batch` | a `_make(**kwargs)` producing a `ClusterReviewTicket` |
| `conversation_factory` | `trigger=Trigger.UPLOAD` | a `_make(**kwargs)` producing a `Conversation` |
| `user_factory` | — | a `_make(**kwargs)` producing a `User` |
| `outbox_message_factory` | `task_name=process_batch`, `payload=dict` | a `_make(**kwargs)` producing an `OutboxMessage` |

A few convenience **instance fixtures** hand back a ready object, not a callable — enough for tests that need a single canonical object:

| Fixture | Returns |
|---|---|
| `batch` | a new `Batch` (via `batch_factory`) |
| `cluster_review_ticket` | an open `ClusterReviewTicket` (`cluster_label=0`) in that batch |
| `closed_cluster_review_ticket` | a closed `ClusterReviewTicket` (`cluster_label=1`, `closed_at` set) in that batch |

`cluster_review_ticket` and `closed_cluster_review_ticket` share the same function-scoped `batch`, matching tests that pair an open and closed ticket in one batch.

```python
def test_open_returns_tickets_without_closed_at(self, cluster_review_ticket, closed_cluster_review_ticket):
    assert list(ClusterReviewTicket.objects.open().values_list("pk", flat=True)) == [cluster_review_ticket.pk]
```

#### Module-local fixtures and helpers

Code reused across several tests *within one file* stays in that file — either as a module-local fixture or a helper, depending on whether the shape varies:

- **Module-local fixture** — when many tests need the same object and its data structure doesn't change. Compose the global factory fixture with the required overrides rather than calling `baker.make` directly. Example: the conversation state fixtures in `test_workflow_models.py`:

  ```python
  @pytest.fixture
  def open_conversation(conversation_factory):
      return conversation_factory(trigger=Trigger.UPLOAD, summary={})

  @pytest.fixture
  def completed_conversation(conversation_factory):
      return conversation_factory(trigger=Trigger.UPLOAD, summary={}, ended_at=timezone.now())

  @pytest.fixture
  def errored_conversation(conversation_factory):
      return conversation_factory(trigger=Trigger.UPLOAD, summary={}, error_message="oops")
  ```

- **Module-local `_make_*` helper** — when per-call variations are needed (each test supplies different fields). Established patterns: `make_images(count)` in `test_process_batch.py`, `_submit_review(client, ticket, keep)` in `test_submit_review.py`, `_create_image(tmp_path, name)` in `test_workflow_models.py`, and the wizard's `_make_identity(**kwargs)`. A fixture that still needs `baker.make` directly is only justified when a factory fixture can't express it (e.g. rows that must be created inline, like `baker.make(OutboxMessage, max_attempts=0)` inside a `pytest.raises`).

#### Variants and multiples

When a test needs a second object or an override (instance fixtures can't express those), call the factory or `baker.make` inline:

```python
other_batch = baker.make(Batch)
ticket = cluster_review_ticket_factory(cluster_label=1)
_created = baker.make(Image, batch=batch, cluster_ticket=ticket, _quantity=120)  # or _quantity for several
```

Users are created with `baker.make(User, username="testuser")` + `set_password(...)` + `save()` so `client.login` works against the hashed password (see `tests/integration/conftest.py`'s `user`/`logged_in_client`).
