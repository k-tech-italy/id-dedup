# Testing

```shell
pytest
```

## The two suites

The split is not organisational, it is a hard rule about what a test may touch.

| Suite | Touches the database | Needs |
|---|---|---|
| `tests/unit/` | **Never.** The ORM is mocked. | Nothing but Python |
| `tests/integration/` | Yes — real Postgres with pgvector | A live database via `DATABASE_URL` |

Unit tests stay fast because the layers they cover are Django-free by construction —
see the [boundary rule](architecture.md#the-two-stage-pipeline). A unit test that reaches a
database has found a boundary violation, not a test-setup problem.

```
tests/
  settings.py          test settings module, forced with --ds
  conftest.py          Django bootstrap, session-scoped pipeline fixtures
  examples/            real face photos, person1/ … person4/
  unit/
    test_images.py     magic-byte validation
    config/            the environment proxy
    wizard/            pipeline, services, models, serializers — ORM mocked
  integration/
    wizard/            4-step wizard views, auth, landing
    workflow/          models, services, views, outbox
```

## Settings

Tests run against `tests/settings.py`, forced by `--ds tests.settings` in
`pyproject.toml`. That beats any exported `DJANGO_SETTINGS_MODULE`, so a shell that is
set up for `runserver` cannot accidentally redirect the suite.

That module seeds a fixed `SECRET_KEY` **before** importing the real settings, so tests
never depend on `.env` for it. `DATABASE_URL` is the exception: it is still read from
the environment through the `env` proxy, because integration tests need to point at a
real database. Unset, it falls back to the proxy default.

## Conventions

- **Flat `def test_...` functions are the default.** `class TestFoo` grouping is fine
  where it genuinely organises a large module.
- **Shared fixtures go in `conftest.py`.** A fixture used by one module can live in
  that module.
- **`@pytest.mark.django_db(transaction=True)`** on anything that hits the ORM.
- **Assert with bare `assert` / `assert not`** for booleans.
- Real images live in `tests/examples/`; synthetic vectors come from the `unit_vector`
  helper. Use synthetic data unless the test is specifically about image decoding or
  face detection.

## Mocking the ORM in unit tests

`_query_candidates` in `dedup/service/proposals.py` is the only real ORM call in the
pipeline and services stack — everything else is pure Python or NumPy. Two levels of
patching cover almost every case:

```python
# Skip the DB entirely when you only care about the proposal logic.
@patch("id_dedup.dedup.service.proposals._query_candidates", return_value=[])
def test_empty_matches(mock_qc, unit_member):
    assert propose_for_members([unit_member]).is_new_identity
```

```python
# Exercise _query_candidates itself: patch both models and inject a chainable queryset.
with (
    patch("id_dedup.dedup.service.proposals.Identity") as MockIdentity,
    patch("id_dedup.dedup.service.proposals.Image") as MockImage,
):
    MockIdentity.objects.filter.return_value = chainable_qs(rows)
    MockImage.objects.filter.return_value = chainable_qs([])
```

Helpers in `tests/unit/wizard/helpers.py`:

| Helper | What it gives you |
|---|---|
| `chainable_qs(rows)` | A queryset mock that chains `filter`/`annotate`/`select_related`/`order_by` and iterates `rows` |
| `mock_identity_row(...)` | A minimal identity row with `.id`, `.display_name`, `.distance`, `.image_count` |
| `unit_vector(seed)` | A deterministic L2-normalised 512-d float32 vector |

!!! note "`@transaction.atomic` bites in unit tests"

    Even with every ORM call mocked, entering the decorator calls
    `connection.get_autocommit()`, which reaches the database. Unit tests around
    `persist_assignments` swap in `mod.persist_assignments.__wrapped__` via an
    `autouse` fixture to get the undecorated function.

The full mock cookbook — every fixture, every pattern — is in
[`.agents/docs/testing.md`](https://github.com/k-tech-italy/id-dedup/blob/develop/.agents/docs/testing.md).

## Documentation is tested too

`tests/test_docs.py` runs a strict documentation build. A broken internal link or a page
missing from a `.pages` nav file fails `pytest`, not just `make docs`. It is skipped
automatically when the `docs` dependency group is not installed, so a `dev`-only
environment still passes.
