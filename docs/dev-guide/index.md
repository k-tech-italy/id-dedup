# Development Guide

This section covers working *on* id-dedup. For the contribution process — branching,
commit format, PR flow — see
[CONTRIBUTING.md](https://github.com/k-tech-italy/id-dedup/blob/develop/CONTRIBUTING.md);
this guide does not restate it.

## Running the full stack

[Setup](setup.md) gets a server up. A complete development environment is four
processes:

| Command | Process | Needed for |
|---|---|---|
| `./manage.py runserver` | Django | Everything. |
| `make dev` | Celery worker **and** beat | The workflow app. Beat drives the outbox sweep; without it nothing is ever dispatched. |
| `NODE_WATCH=1 npm run build` | Webpack | Frontend changes. |
| `make docs-serve` | properdocs | These docs. |

`make worker` and `make beat` run the two Celery processes separately if you would
rather see their logs apart. All three Celery targets require `REDIS_URL` and fail
early if it is unset.

## Useful commands

```shell
pytest                          # test suite; integration tests need live Postgres
ruff check . && ruff format .   # lint and format
./manage.py seed_tickets        # 20 open review tickets with dummy images
./manage.py dispatch_outbox     # drain the outbox by hand
./manage.py dispatch_outbox --dead          # list dead-lettered rows
./manage.py dispatch_outbox --requeue-dead  # reset them for another attempt
make clean                      # drop build artefacts, node_modules and .venv
```

## Working on these docs

The site is built with [properdocs](https://pypi.org/project/properdocs/), an mkdocs
distribution, configured in `properdocs.yml` at the repository root.

```shell
uv sync --group docs    # once
make docs-serve         # live reload on http://127.0.0.1:8001
make docs               # build into ~build/docs
```

Pushing to `develop` publishes the site to
<https://k-tech-italy.github.io/id-dedup/> via `.github/workflows/docs.yml`. Pull
requests run the same strict build but do not deploy, so a broken link fails review
rather than the live site.

Points to know before you edit:

- **Build with `properdocs`, never `mkdocs`.** The CLI looks for `properdocs.yml` first
  and errors on a bare `mkdocs.yml` unless it is passed with `-f`. The Makefile targets
  do the right thing.
- **The build is `strict`.** A broken internal link or a missing file fails it rather
  than warning. `pytest` runs a strict build too, so a bad link fails the suite.
- **Navigation comes from `.pages` files**, not from a `nav:` block in the config —
  one per directory, listing its pages in order. A new page must be added to its
  directory's `.pages` or the strict build rejects it.
- **`.gitignore` ignores dotfiles** (`.*` on line 1), with an explicit `!**/.pages`
  negation to let nav files through. If you add another dotfile under `docs/`, it needs
  its own negation or it will work locally and be missing for everyone else.
- **CI installs `--only-group docs`, not `--group docs`.** The workflow skips the
  application dependencies because nothing in the docs build needs them. That is true
  only while `mkdocstrings` has no `:::` directive to resolve — add one and
  `.github/workflows/docs.yml` must switch to a full `uv sync --group docs`.
- **Mermaid diagrams need a network at view time.** ```` ```mermaid ```` fences are
  rendered by the Material theme, which pulls the renderer from a CDN in the browser.
  The build itself is offline; a reader with no network sees the diagram source instead.
- **`docs/specs/` is excluded from the site** via `exclude_docs`. Requirement documents
  are tracked in git but not published.
- **Autodoc is deliberately limited.** `mkdocstrings` is configured over `src/`, but is
  only used against the Django-free modules — `id_dedup.ml.pipeline` and
  `id_dedup.images`. Pulling in `models.py` would make the docs build import Django and
  therefore need settings and a database. The model documentation in
  [Reference → Models](reference/models.md) is hand-written for that reason; keep it
  that way.

## Where documentation lives

Three places, with three different audiences. Put a change in exactly one of them.

| Topic | Canonical source |
|---|---|
| Contribution workflow, branching, commit format, PR process | `CONTRIBUTING.md` — this site links to it and never restates it |
| Agent-facing map of the source tree | `AGENTS.md` and `.agents/docs/` |
| Install, usage, architecture narrative, reference | `docs/` — this site |

`README.md` keeps a short intro and setup and links onward. New prose belongs in
`docs/`.
