# Contributing to id-dedup

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python package and environment manager
- [Ruff](https://docs.astral.sh/ruff/installation/) — linting and formatting
- [PostgreSQL](https://www.postgresql.org/) with the [pgvector](https://github.com/pgvector/pgvector) extension enabled
- (Optional) [direnv](https://direnv.net/docs/installation.html)

For detailed local setup, see [README.md](./README.md).

## Branching model

- `develop` — integration branch. Target all PRs here.
- `main` — release branch. Only maintainers merge release PRs here.
- Feature and task work lives on short-lived branches cut from `develop`.

## Branch naming

- Feature branches: `feat/<ticket-number-if-avail>-short-description`
- Bug/fix branches: `fix/<ticket-number-if-avail>-short-description`
- Task branches: `task/<ticket-number-if-avail>-short-description`

## Commits

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

| Type | When to use |
|------|-------------|
| `fix:` | bug fix |
| `feat:` | new feature |
| `chore:` | maintenance, deps, config |
| `test:` | test-only changes |
| `docs:` | documentation only |
| `BREAKING CHANGE:` | incompatible API change |

Scope hints (optional): `(ui)`, `(dedup)`, `(views)`, `(services)`.

## Pull requests

1. Push your branch and open a PR to `develop`.
2. Ensure all automated checks pass (tests, lint).
3. Request a review from at least one maintainer.
4. Once approved, the PR author merges (if they have rights); otherwise a maintainer merges.

## Running tests

```shell
pytest
```

Unit tests are in `tests/unit/` and mock the ORM — no live database required. Conventions:

- Flat functions (`def test_...`) are the default. Class-grouped tests (`class TestFoo`) are acceptable when they improve organisation.
- Shared fixtures go in `conftest.py`. Module-specific fixtures that won't be reused elsewhere may be defined in the test file itself.
- Use `@pytest.mark.django_db(transaction=True)` for any test that hits the ORM.

See [`.agents/docs/testing.md`](.agents/docs/testing.md) for mock patterns and what to avoid.

## Code style

Linting and formatting via [Ruff](https://docs.astral.sh/ruff/) (line-length 120, target py313):

```shell
ruff check .
ruff format .
```

Key rules:

- Use `from __future__ import annotations` only where needed (e.g. forward references that can't be resolved at definition time).
- Docstrings should be as descriptive as needed. Inline comments explain *why*, not *what*; multi-line comments are fine when a single line can't capture a design decision.
- No Django Forms, no DRF. Prefer function-based views; class-based views are acceptable when they meaningfully reduce complexity (e.g. dispatching multiple HTTP verbs).

## Architecture rules

- `pipeline.py` must have zero Django imports — it is Django-free so the ML layer can be tested without a DB.
- `services.py` owns all ORM interaction. No ML/NumPy computation there.
- Only `complete()` in `views.py` writes to the database.

See [`.agents/docs/dedup-flow.md`](.agents/docs/dedup-flow.md) for the full pipeline walkthrough.

## Code of conduct

Be respectful and constructive.
