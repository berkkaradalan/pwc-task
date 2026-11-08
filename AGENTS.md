# AGENTS.md

Working conventions for this repository, for AI coding agents and human contributors alike.
Read them before starting a change. If a task conflicts with a rule, stop and ask instead of
silently choosing one.

## Project

On-call RAG answers on-call engineers' questions from incident runbooks, with sources, and
returns `no_relevant_context` when the runbooks do not cover a question. It has two services
that run as separate processes: a one-shot ingestion job and a FastAPI question-answering API.

```
src/oncall_rag/
├── shared/   # settings, models, ports, adapters (embeddings, llm, pgvector), errors, logging
├── ingest/   # python -m oncall_rag.ingest [--reindex]
└── query/    # uvicorn oncall_rag.query.main:app --no-access-log
```

`ingest` and `query` never import each other; shared code lives only in `shared`.
[docs/architecture.md](docs/architecture.md) describes the flows, and
[docs/decisions.md](docs/decisions.md) the reasons behind them.

## Commands

Run everything from the repository root.

```bash
uv sync                          # install from uv.lock
uv run pytest --cov=oncall_rag   # unit tests
uv run pytest -m integration     # needs INTEGRATION_DATABASE_URL, see Tests
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests            # strict
uv lock --check
docker compose config -q         # needs .env (copy .env.example)
OLLAMA_MODELS_DIR=./.cache/ollama-models scripts/e2e.sh   # end to end, local mode
```

`RUN_MODE` in `.env` (`local` or `cloud`) is the only switch between Ollama and OpenRouter.
Change it in this order: `docker compose down`, edit `RUN_MODE`, then
`docker compose up -d --build --wait api`. If the order was missed,
`docker compose --profile '*' down` stops every service, including Ollama.

## Architecture rules

- Ports are `abc.ABC` classes with `@abstractmethod` in `shared/ports.py`. Do not use
  `typing.Protocol`. A port exists only if it has a real adapter and a test fake.
- Every adapter and every fake inherits explicitly from the ports it implements.
- Concrete adapters are built only in the composition roots: `ingest/wiring.py` and
  `query/core/wiring.py`. Everything else depends on the ports.
- Provider-specific code (Ollama, OpenRouter, pgvector) lives in `shared/adapters/`. The one
  exception is `shared/config.py`, the only place that turns `RUN_MODE` into an endpoint, a
  key and a model.
- SQL lives only in the pgvector adapter.
- All configuration goes through the pydantic-settings classes in `shared/config.py`; no
  `os.getenv` elsewhere. Required infrastructure settings have no default; only app-level
  tunables do.
- No module-level mutable state. Per-request data lives only in the graph state of that
  request.
- Paths come from settings and are relative to the working directory, never from
  `__file__`.

## Code rules

- English only: code, identifiers, comments, docstrings, logs, errors, docs and commit
  messages.
- Names say what a thing is or does. Avoid `data`, `info`, `helper`, `manager`, `utils`,
  `handle`, `process` and abbreviations other than well-known ones (`url`, `id`, `db`).
  Booleans read as questions: `is_valid`, `has_changes`.
- Entry points and orchestration functions read like a table of contents: a short sequence
  of well-named calls. Keep one level of abstraction per function, functions under about 25
  lines, nesting at most two levels deep, and prefer early returns.
- No clever one-liners, nested ternaries or f-strings with logic inside; compute values
  first, then format.
- No magic numbers or strings: name constants, with a comment when the value needs a
  reason (`CANDIDATES_PER_METHOD`, `HISTORY_MAX_TOTAL_CHARS`). Group related constants in an
  `Enum` next to the code that owns them. A user-facing message used once stays inline.
- Comments explain why, never what, and never the history of a change. Docstrings only where
  the name and signature do not already say it.
- No bodies that are only `...` or `pass`. No speculative parameters, settings or extension
  points, no wrappers that only forward a call, no defensive checks for states that cannot
  happen, no dead code.
- `# type: ignore` needs a one-line reason, as in `# type: ignore[call-arg]  # values come
  from the environment`. To narrow a type, use `assert x is not None  # narrows the type: ...`.
- Prefer a mature library over hand-written code: pydantic-settings for settings,
  langchain-text-splitters for splitting, python-frontmatter for front matter. A new
  dependency needs a stated reason, a comment in `pyproject.toml`, and is pinned through
  `uv.lock`.

## Errors

- Adapters let library exceptions (`openai`, `psycopg`) propagate unchanged. An adapter may
  catch one only to add a note naming the call (`error.add_note(...)`) and re-raise it.
- Define an own exception only for a rule no library expresses (`shared/errors.py`:
  `ReindexRequiredError`, `ProviderResponseError`). Exceptions describe what happened and
  know nothing about HTTP or exit codes.
- Entry points translate exceptions: the `/ask` route into 502, 503 or 504 with a fixed
  `detail`, and `ingest/__main__.py` into exit codes 1 and 2. Log the error once, at that
  boundary, with `failure_summary`.
- Catch bare `Exception` only at the outermost boundary, and log it. Never swallow an
  exception. Only `if __name__ == "__main__"` calls `sys.exit()`.

## Tests

- Unit tests use the fakes in `tests/fakes.py` and small fixture files under
  `tests/fixtures/`. They need no network, no database and no API key.
- Test behaviour, not implementation details. Every behaviour change comes with a test.
- Never delete, skip or weaken a test to make it pass. A refactor keeps every existing
  test green without changing what it asserts.
- Tests that need real services are marked `@pytest.mark.integration`, which is excluded by
  default. They live in `tests/integration/` and run against a throwaway PostgreSQL with
  pgvector. `INTEGRATION_DATABASE_URL` names its `postgres` maintenance database, and each
  test creates and drops its own database. Locally:

  ```bash
  docker run -d --rm --name oncall-rag-it -e POSTGRES_HOST_AUTH_METHOD=trust \
    -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.6-pg17-bookworm
  until docker exec oncall-rag-it pg_isready -h 127.0.0.1 -U postgres -q; do sleep 1; done
  INTEGRATION_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/postgres \
    uv run pytest -m integration
  docker rm -f oncall-rag-it
  ```

  Without the variable they are skipped; in CI they fail instead.
- The full stack is covered by `scripts/e2e.sh`.

## Docs

A change that affects behaviour, configuration or a measurement updates `README.md` and
`docs/` in the same change. The README is for a reviewer: what the project does, how to run
it, design decisions and limitations, without marketing language. Docs never describe a
contributor's machine or local setup.

## Safety

- Secrets live only in `.env`, which is git-ignored. The repository has `.env.example` with
  placeholder values.
- Never print or log secrets or raw configuration values. Settings are `SecretStr` where
  they hold credentials, and validation errors hide input values.
- No absolute paths, home directories, usernames, hostnames or local tool locations in code,
  config, tests or docs, and no defaults tailored to one machine.
- Never read or reference files outside the repository.

## Git

- Agents do not commit, push, or rewrite history unless asked. Never change a remote
  without an explicit request.
- Never untrack, delete or move files in git without asking.
- A commit message has an imperative subject under 72 characters naming the main change,
  and a short body saying what changed and why. It carries no AI attribution: no
  `Co-Authored-By` trailer, no "Generated with" line, no session links.

## Done checklist

Before reporting a change as done:

- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src tests`,
      `uv lock --check` and `uv run pytest` pass.
- [ ] `docker compose config -q` passes if Compose files or `.env.example` changed.
- [ ] Every changed line has a reason tied to the task; no rule above is broken.
- [ ] README and `docs/` match the change.
- [ ] No secrets, absolute paths or non-English text were added.
- [ ] The report says what changed, why, what was decided without being asked, and anything
      left unverified or in conflict with these rules.
