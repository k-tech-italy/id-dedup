# id-dedup

4-step wizard web app that clusters uploaded face images and proposes matches against a database of known identities. All identity assignment is manual — the pipeline never auto-assigns.

## Stack

- **Backend**: Django 5.2, PostgreSQL, pgvector (512-d HNSW cosine index)
- **ML**: InsightFace (ArcFace embeddings), scikit-learn (DBSCAN), OpenCV, NumPy
- **Frontend**: HTMX 2, Alpine.js 3, SortableJS, Tailwind CSS v4, Webpack
- **Python**: 3.13+, managed with [uv](https://docs.astral.sh/uv/)

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python package and environment manager
- [Node.js](https://nodejs.org/) (via [nvm](https://github.com/nvm-sh/nvm) recommended) — for the JS bundle
- [PostgreSQL](https://www.postgresql.org/) with the [pgvector](https://github.com/pgvector/pgvector) extension enabled
- (Optional) [direnv](https://direnv.net/docs/installation.html) — for automatic environment loading

## Setup

Clone the repo and switch to your working branch:

```shell
git clone <repo-url>
cd id-dedup
git switch <your-working-branch>
```

Prepare the Python environment:

```shell
uv python install 3.13
uv venv
uv sync
```

Install JS dependencies:

```shell
npm install
```

Copy the example env file and configure it:

```shell
cp .env.example .env
```

Edit `.env` — at minimum set `DATABASE_URL` to point at your PostgreSQL instance and set `SECRET_KEY`.

Apply migrations:

```shell
./manage.py migrate
```

If using `direnv`:

```shell
direnv allow
```

> **Shortcut**: `make develop` runs `npm install`, `uv venv`, `uv sync`, and `direnv allow` in one step (requires direnv to be installed).

## Running

Start the dev server:

```shell
./manage.py runserver
```

In a second terminal, build the JS bundle:

```shell
npm run build               # one-shot
NODE_WATCH=1 npm run build  # watch mode during development
```

## Usage

Navigate to `http://localhost:8000/wizard/upload/` to start a deduplication batch:

1. **Upload** — drop face images (any count, mixed people).
2. **Review** — inspect DBSCAN clusters; drag images between clusters or use the split drawer to correct groupings.
3. **Adjudication** — for each cluster, accept a proposed identity match, search for an existing one, or create a new identity.
4. **Complete** — confirm; images and embeddings are saved to the database.

## Development

```shell
pytest          # run the test suite
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for code conventions, architecture rules, and the PR workflow.
See [AGENTS.md](AGENTS.md) for a full technical reference including the deduplication pipeline internals.