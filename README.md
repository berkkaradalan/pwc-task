# pwc-task

[![CI](https://github.com/berkkaradalan/pwc-task/actions/workflows/ci.yml/badge.svg)](https://github.com/berkkaradalan/pwc-task/actions/workflows/ci.yml)

Experienced software developer transitioning to GenAI home assignment

## Running ingestion

Ingestion reads every `.md` and `.txt` file under `DOCS_DIR` (default `data/docs`),
chunks and embeds it, and stores it in PostgreSQL with pgvector.

First create your configuration (all commands run from the repository root; `.env` and
`DOCS_DIR` are read relative to the working directory):

```bash
cp .env.example .env    # then set POSTGRES_PASSWORD and the matching DATABASE_URL
```

### With Docker Compose

```bash
docker compose up --build ingest
```

This starts `db` (PostgreSQL + pgvector) and `ollama`, pulls `EMBEDDING_MODEL` only if it
is not present yet (`ollama-init`, so later runs work offline), then runs the one-shot
`ingest` service. `data/docs` is mounted read-only, so after editing documents just run
the command again.

### Locally with uv

Run the database and Ollama from Compose, and the ingestion code on the host:

```bash
docker compose up -d db ollama ollama-init
uv sync
uv run python -m oncall_rag.ingest
```

`DATABASE_URL` and `EMBEDDING_BASE_URL` in `.env` point at the ports Compose publishes
(`POSTGRES_HOST_PORT`, `OLLAMA_HOST_PORT`); change both together if a port is taken.

### Re-indexing

```bash
uv run python -m oncall_rag.ingest --reindex
# or: docker compose run --rm ingest python -m oncall_rag.ingest --reindex
```

`--reindex` drops and recreates the tables with the current `EMBEDDING_DIM` and indexes
every document again. It is required after changing `EMBEDDING_MODEL` or `EMBEDDING_DIM`
(ingestion refuses to run otherwise) and recommended after changing the chunk settings.

The command exits with `0` on success, `1` on fatal errors (database or Ollama
unreachable, index incompatible with the configuration) and `2` on invalid configuration.

## Ingestion design

**Flow.** `load -> hash -> skip unchanged -> chunk -> embed -> replace -> prune`
(`ingest/pipeline.py`). The pipeline only depends on the `Embedder` and `VectorStore`
ports; `ingest/wiring.py` wires the Ollama and pgvector adapters.

**Loading.** Optional YAML front matter (parsed with `python-frontmatter`) provides
`title`, `service`, `owner_team` and `last_reviewed`; the title falls back to the file
name. Front matter that parses but is unusable (not closed, or not a mapping) is ignored
with a warning and the file is still loaded, while front matter with a YAML syntax error
skips the file with a warning. Files with an unsupported extension, no content or invalid
UTF-8 are also skipped with a warning; one bad file never stops the run.

**Chunking.** Markdown is split on H1–H3 headings with langchain's
`ExperimentalMarkdownSyntaxTextSplitter`, which keeps whitespace and code fences intact
(so `#` lines inside code are not headings and code indentation survives). Blocks under
the same heading are joined again, so a code sample stays with the text that explains
it, and each chunk keeps its heading path, e.g. `Payments outage > Mitigation >
Rollback`. `.txt` files have no headings. Any section longer than `CHUNK_MAX_CHARS` is
split by `RecursiveCharacterTextSplitter` on paragraph, then line, then sentence
boundaries, with up to `CHUNK_OVERLAP_CHARS` of overlap. The embedder receives
`<title> > <heading path>` followed by the chunk text, so short chunks keep their
context; the raw chunk text is stored separately for citations. Chunking is pure and
deterministic.

**Idempotency.** Each document stores the SHA-256 of its raw file bytes. Unchanged files
are skipped without calling the embedder, so a second run in a row does no embedding
work. A changed file has its chunks replaced in a single transaction. Indexed documents
that were not loaded in this run (deleted, or now invalid) are removed. The hash covers
file content only, so changing the chunk settings needs `--reindex`.

**Embeddings.** `qwen3-embedding:0.6b` via Ollama: it runs locally on CPU, is
multilingual, produces 1024-dimensional vectors and works with long inputs. It is
instruction-aware, so queries get an instruction prefix and documents do not; this
detail stays in the Ollama adapter. Requests are batched (`EMBEDDING_BATCH_SIZE`), use
explicit timeouts, retry connection errors and 5xx responses up to three attempts with
exponential backoff (`tenacity`), and every returned vector is checked against
`EMBEDDING_DIM`.

**Storage.** `documents` and `chunks` (with `ON DELETE CASCADE`) in PostgreSQL, with an
HNSW index using cosine distance, which limits `EMBEDDING_DIM` to 2000. The application
creates the tables, because the vector column dimension comes from `EMBEDDING_DIM`;
`sql/init.sql` only enables the `vector` extension. Each document records its embedding
model. If the stored model or dimension differs from the configuration, ingestion stops
and asks for `--reindex`, so vectors from different models are never mixed.

## Quality checks

CI (`.github/workflows/ci.yml`) runs on pushes to `main` and on pull requests. Every check
runs locally with the same command, from the repository root:

```bash
# Python: lint, formatting, lockfile, types (strict, src/ only), unit tests with coverage
uv run ruff check .
uv run ruff format --check .
uv lock --check
uv run mypy src
uv run pytest --cov=oncall_rag

# Dockerfile, Compose file (needs .env), workflow files, secrets in the git history
docker run --rm -i hadolint/hadolint:v2.15.1 < Dockerfile
docker compose config -q
docker run --rm -v "$PWD:/repo:ro" -w /repo rhysd/actionlint:1.7.12
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/repo:ro" \
  zricethezav/gitleaks:v8.30.1 git --redact --no-banner /repo

# Image scan: fails only on CRITICAL vulnerabilities that have a fix
docker build -t oncall-rag:ci .
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy:0.76.1 image --severity CRITICAL --ignore-unfixed --exit-code 1 oncall-rag:ci

# End to end (needs .env)
OLLAMA_MODELS_DIR=./.cache/ollama-models scripts/e2e.sh
```

`scripts/e2e.sh` starts the Compose stack with the `compose.ci.yml` override (its own
project name, no published ports), runs `ingest` on the sample documents in `data/docs`,
and fails unless it exits with `0` and the `chunks` table is not empty. Ollama models are
kept in `OLLAMA_MODELS_DIR`, which CI caches by model name; the Ollama container writes
that folder as root.

`uv run pre-commit install` runs ruff, end-of-file and trailing-whitespace fixes, and
gitleaks on staged changes before each commit.

## Next steps

- Type-check `tests/` with mypy too. This needs a `py.typed` marker in the package and
  one module name per test file (today `tests/fakes.py` resolves as both `fakes` and
  `tests.fakes`).
- Enable Dependabot (grouped updates, patch-only for Python) for long-term maintenance.

## Project structure

Each service is its own package and imports only from `shared`; the question-answering
service will be a sibling of `ingest`.

```
oncall-rag/
├── src/oncall_rag/
│   ├── shared/                 # code used by every service
│   │   ├── config.py           # pydantic-settings
│   │   ├── logging.py
│   │   ├── errors.py           # application exceptions
│   │   ├── models.py           # Document, Chunk
│   │   ├── ports.py            # Protocols: Embedder, VectorStore
│   │   └── adapters/
│   │       ├── embeddings/ollama.py
│   │       └── vectorstore/pgvector.py
│   └── ingest/                 # `python -m oncall_rag.ingest`
│       ├── __main__.py         # entry point, exception -> exit code mapping
│       ├── wiring.py           # composition root: builds adapters from settings
│       ├── loaders.py          # .md/.txt reading, front matter, validation
│       ├── chunker.py
│       └── pipeline.py         # load -> hash -> chunk -> embed -> replace -> prune
├── tests/
│   ├── fakes.py                # FakeEmbedder, InMemoryVectorStore
│   ├── fixtures/docs/          # small test documents
│   └── unit/
├── data/docs/                  # sample documents to ingest
├── sql/init.sql                # enables the pgvector extension
├── scripts/e2e.sh              # end-to-end check against the Compose stack
├── .github/workflows/ci.yml    # lint, test, build + image scan, end to end
├── Dockerfile
├── docker-compose.yml
├── compose.ci.yml              # override used by scripts/e2e.sh
├── pyproject.toml / uv.lock
├── .pre-commit-config.yaml
├── .env.example
└── README.md
```
