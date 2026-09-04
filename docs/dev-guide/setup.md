# Setup

The shortest path from a fresh clone to a running server. Once it is up, the
[Overview](index.md) covers the rest of the development environment — Celery, the
outbox reaper, the frontend build.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python 3.13+ toolchain
- [Node.js](https://nodejs.org/) — for the JS bundle (see `.nvmrc` for the version)
- [PostgreSQL](https://www.postgresql.org/) with the
  [pgvector](https://github.com/pgvector/pgvector) extension available
- [Redis](https://redis.io/) — only needed once you run Celery

!!! note "pgvector must be installed, not just available"

    The initial migration creates a `vector` column and an HNSW index. If the extension
    is missing from the target database, `migrate` fails. Install the extension package
    for your Postgres version, then let the migration enable it.

## 1. Get the code and the dependencies

```shell
git clone https://github.com/k-tech-italy/id-dedup.git
cd id-dedup

uv python install 3.13
uv venv
uv sync

npm install
```

!!! tip "Shortcut"

    `make develop` runs `npm install`, `uv venv`, `uv sync` and `direnv allow` in one
    step. It requires [direnv](https://direnv.net/).

## 2. Configure the environment

```shell
cp .env.example .env
```

`SECRET_KEY` is the only variable with no default — everything else falls back to a
local-development value. Set it, and point `DATABASE_URL` at your database:

```shell
DATABASE_URL=postgres://postgres:postgres@localhost:5432/id_dedup
SECRET_KEY=django-insecure-change-me-for-local-dev
```

The full list is in [Reference → Settings](reference/settings.md).

## 3. Create the schema

```shell
./manage.py migrate
./manage.py createsuperuser
```

## 4. Run it

```shell
./manage.py runserver
```

In a second terminal, build the frontend bundle:

```shell
npm run build               # one-shot
NODE_WATCH=1 npm run build  # rebuild on change
```

Then sign in at <http://localhost:8000/> and pick a front end:

| URL | What you get |
|---|---|
| `/wizard/upload/` | The [4-step wizard](../user-guide/wizard.md) — synchronous, session-backed. |
| `/workflow/upload/` | The [ticket workflow](../user-guide/tickets.md) — needs Celery running, see below. |
| `/admin/` | Django admin over batches, conversations, tickets and the outbox. |

## 5. Only if you use the workflow app

The workflow app does its clustering off the request cycle, and dispatches through a
durable outbox that a scheduled sweep drains. Nothing happens after an upload until both
a worker and the beat scheduler are running:

```shell
make dev    # Celery worker + beat in one terminal; requires REDIS_URL
```

To see the ticket UI without uploading anything real:

```shell
./manage.py seed_tickets    # 20 open tickets with dummy images
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `ImproperlyConfigured: SECRET_KEY not found` | `.env` is missing or `SECRET_KEY` is unset — it is the one mandatory variable. |
| `type "vector" does not exist` during `migrate` | pgvector is not installed in the target Postgres instance. |
| Upload succeeds but no tickets ever appear | No Celery worker, or no beat scheduler to sweep the outbox. Run `make dev`, or drain it by hand with `./manage.py dispatch_outbox`. |
| First upload takes ~30 s | InsightFace downloads and loads its model on first use, then caches it for the process lifetime. |
