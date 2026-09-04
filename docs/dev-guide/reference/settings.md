# Settings

Every environment variable is read through a single proxy —
`src/id_dedup/config/environment.py` — which owns the cast and the default for each
name. Settings modules never call `decouple.config()` directly.

Values come from the process environment or `.env` (via
[python-decouple](https://pypi.org/project/python-decouple/); `direnv` will load `.env`
for you if you use it). Copy `.env.example` to `.env` to start.

## Variables

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | str | **none — required** | Django secret key |
| `DEBUG` | bool | `False` | Django debug mode |
| `DATABASE_URL` | str | `postgresql://postgres:@127.0.0.1:5432/id_dedup` | PostgreSQL DSN; the database needs pgvector |
| `ALLOWED_HOSTS` | csv | `localhost,127.0.0.1` | Django `ALLOWED_HOSTS` |
| `SECURE_SSL_REDIRECT` | bool | `False` | Redirect HTTP to HTTPS |
| `SECURE_HSTS_SECONDS` | int | `0` | HSTS max-age; `0` disables |
| `REDIS_URL` | str | `redis://localhost:6379/0` | Celery broker **and** the Django cache |
| `REDIS_RESULT_URL` | str | `redis://localhost:6379/1` | Celery result backend |
| `OUTBOX_MAX_ATTEMPTS` | int | `5` | Dispatch attempts before a message is dead-lettered |
| `OUTBOX_SWEEP_SECONDS` | int | `60` | Seconds between outbox sweeps |

!!! warning "`SECRET_KEY` is the only one with no default"

    An unset `SECRET_KEY` raises `ImproperlyConfigured` at startup. That is deliberate —
    every other variable can fall back to something sane for local development, and this
    one must not.

## Notes on individual variables

**`DATABASE_URL`** — the target database must have pgvector available. The initial
migration creates `vector` columns and HNSW indexes and will fail without it.

**`REDIS_URL`** is used twice: as the Celery broker and as the Django cache backend.
`REDIS_RESULT_URL` points at a *different* logical database (`/1` by default) so results
do not share a keyspace with the broker.

**`OUTBOX_MAX_ATTEMPTS`** caps the retries for one message. Because a failing message is
attempted at most once per sweep, the practical time to dead-letter is roughly
`OUTBOX_MAX_ATTEMPTS × OUTBOX_SWEEP_SECONDS`. A dead-lettered row is never swept again;
recover it with `./manage.py dispatch_outbox --requeue-dead`.

**`OUTBOX_SWEEP_SECONDS`** feeds the Celery beat schedule entry `dispatch-outbox`. It is
read at startup, so changing it needs a beat restart. It is also the floor on how long
an upload can sit before clustering begins.

!!! note "`.env.example` shows `OUTBOX_SWEEP_SECONDS=10.0`"

    That is a commented-out suggestion for a snappier development loop, not the default.
    The proxy default is `60`, and the cast is `int`.

## Test settings

Tests do not use the values above for `SECRET_KEY` or `DEBUG`. They run against
`tests/settings.py`, forced with `--ds tests.settings`, which seeds a fixed key before
importing the real settings module. `DATABASE_URL` *is* still read from the environment,
because integration tests need a real database. See
[Testing](../testing.md#settings).
