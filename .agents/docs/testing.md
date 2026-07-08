# Testing guide

## Running tests

```
pytest
```

`DATABASE_URL` must be set in the environment. `tests/conftest.py` calls `django.setup()` using it. Tests that touch the ORM need `@pytest.mark.django_db(transaction=True)`.

---

## File layout

```
tests/
  conftest.py               session-scoped pipeline fixture; real images at tests/examples/
  examples/
    person1/ … person4/     real face photos used by pipeline integration fixtures
  unit/
    conftest.py             synthetic unit fixtures — no DB, no images
    helpers.py              chainable_qs, mock_image_row, unit_vector
    test_pipeline.py        extract_embedding, process_images, ClusterResult.split
    test_services.py        ClusterProposal properties, propose_for_members, propose_matches, _query_candidates
    test_views_wizard.py    wizard view tests
```

---

## Unit test conventions

- **Flat functions** (`def test_...`) are the default.
- **Class-grouped** tests (`class TestFoo`) are acceptable where they improve organisation.
- Shared fixtures go in `conftest.py`. Module-specific fixtures that won't be reused elsewhere may be defined in the test file itself.
- `tests/unit/` tests must not touch the real DB directly. Mock the ORM (see below).

---

## Mocking the ORM

`_query_candidates` in `services.py` is the only real ORM call in the pipeline/services stack. Everything else is pure Python or NumPy.

### Mocking `propose_for_members` / `propose_matches`

Patch `_query_candidates` at the module level to skip the DB entirely:

```python
from unittest.mock import patch
from id_dedup.dedup.services import propose_for_members

@patch("id_dedup.dedup.services._query_candidates", return_value=[])
def test_empty_matches(mock_qc, unit_member):
    proposal = propose_for_members([unit_member])
    assert proposal.is_new_identity
```

### Mocking `_query_candidates` itself

Patch `Image` at the module level and inject a `chainable_qs`:

```python
from unittest.mock import patch, MagicMock
from tests.unit.helpers import chainable_qs, mock_image_row
from id_dedup.dedup.services import _query_candidates

def test_collapses_per_identity(unit_member_embedding):
    rows = [
        mock_image_row(identity_id=1, display_name="Alice", distance=0.1),
        mock_image_row(identity_id=1, display_name="Alice", distance=0.2),
    ]
    with patch("id_dedup.dedup.services.Image") as MockImage:
        MockImage.objects.filter.return_value = chainable_qs(rows)
        results = _query_candidates(unit_member_embedding, top_k=5, min_similarity=0.6, similarity_band=0.1)
    assert len(results) == 1
    assert results[0].matched_image_count == 2
```

### Helper reference

| Helper | Signature | What it does |
|---|---|---|
| `chainable_qs(rows)` | `list → MagicMock` | Queryset mock that chains `filter/annotate/select_related/order_by` and iterates `rows` |
| `mock_image_row(identity_id, display_name, distance)` | `→ MagicMock` | Minimal Image row mock with `.identity_id`, `.distance`, `.identity.display_name` |
| `unit_vector(seed)` | `int → np.ndarray` | Deterministic L2-normalised 512-d float32 vector |

---

## View tests

View tests use Django's test client. Session state is set up via helper functions defined at the top of `test_views_wizard.py` (not fixtures):

- `_setup_result(client, result)` — serialises a `ClusterResult` into the session.
- `_setup_proposals(client, proposals, adj_index)` — serialises proposals and sets `adj_index`.

These call the view-layer serialisation helpers directly (`_serialize_result`, `_serialize_proposal`), not raw JSON.

Mark any test that hits the ORM with `@pytest.mark.django_db(transaction=True)`. This includes `search`, `complete`, and `assign` with a DB-backed identity. Tests that only exercise session logic and return HTML fragments generally do not need it.

HTMX redirect responses carry an `HX-Redirect` header; the Django test client exposes the final URL as `resp.url`. Check that, not the response body.

---

## What not to do

- Do not put shared/reusable fixtures inside test files — those belong in `conftest.py`.
- Do not write DB-hitting tests in `tests/unit/` outside the view tests. Mock the ORM.
- Do not use `@pytest.mark.django_db` without `transaction=True` — session-backed tests need it.

---

## Future: integration tests

`tests/integration/` does not exist yet. When it does, it will contain tests that require a live PostgreSQL + pgvector instance and real embeddings. Keep those separate from `tests/unit/`.
