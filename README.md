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

This starts `db` (PostgreSQL + pgvector) and `ollama`, pulls the models listed in
`OLLAMA_PULL_MODELS` that are not present yet (`ollama-init`, so later runs work offline),
then runs the one-shot `ingest` service. `data/docs` is mounted read-only, so after editing documents just run
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

## Running the API

The question-answering service (`query`) answers questions from the indexed documents.

```bash
docker compose up -d --build --wait api
```

This runs ingestion first (`api` starts only after `ingest` completed successfully), then
serves the API on `127.0.0.1:${API_HOST_PORT}` (default 8000). The container is healthy
once `GET /health` answers `200`. To run the API on the host instead, start
`docker compose up -d db ollama ollama-init`, set `LLM_BASE_URL` in `.env` to the host
address (`http://localhost:${OLLAMA_HOST_PORT}/v1` in local mode), and run
`uv run uvicorn oncall_rag.query.main:app`.

### API

`POST /ask` takes `{"question": "..."}` (surrounding whitespace is stripped; 1 to 1000
characters) and returns the answer, a status (`answered` or `no_relevant_context`), and
the passages it was based on. Sources come from retrieval, never from the model output.

A question the documentation covers:

```bash
curl -s localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question": "How do I mitigate a payments worker queue backlog?"}'
```

```json
{
  "answer": "1. Scale up worker pods if the workers are healthy but processing is lagging: ...",
  "status": "answered",
  "sources": [
    {
      "source": "payments-worker-queue-backlog.md",
      "title": "Payments Worker Queue Backlog Runbook",
      "heading_path": "Mitigation",
      "score": 0.725,
      "excerpt": "Apply the following remediation steps according to the findings: ..."
    }
  ]
}
```

An unrelated question returns a fixed answer without calling the language model:

```bash
curl -s localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question": "What is the capital of France?"}'
```

```json
{"answer": "No relevant documentation was found for this question.", "status": "no_relevant_context", "sources": []}
```

Invalid input returns FastAPI's default `422` body, which lists each invalid field:

```bash
curl -s localhost:8000/ask -H 'Content-Type: application/json' -d '{"question": "   "}'
```

```json
{"detail": [{"type": "string_too_short", "loc": ["body", "question"], "msg": "String should have at least 1 character", "input": "   ", "ctx": {"min_length": 1}}]}
```

`GET /health` returns `200 {"status": "ok"}` when the database answers a trivial query and
`503 {"detail": "failed"}` otherwise; it never calls the language model. The OpenAPI docs
are at `/docs`.

Errors use FastAPI's default bodies. The `/ask` route translates the application
exceptions into `HTTPException` with a short, fixed `detail`; the exception text, which may
name internal hosts, is only logged.

| Status | Body | Cause |
|---|---|---|
| 422 | `{"detail": [...]}` | the request body is invalid |
| 502 | `{"detail": "..."}` | the language model or embedding service rejected the request (e.g. bad API key, unknown model) or gave no answer |
| 503 | `{"detail": "..."}` | the language model, the embedding service or the database is unreachable, overloaded (429) or failing (5xx) |
| 504 | `{"detail": "..."}` | the language model did not answer within `LLM_TIMEOUT_SECONDS` |
| 500 | `Internal Server Error` (plain text) | anything unexpected; the traceback is only in the logs |

## Running modes

Switching between cloud and local is a configuration change only: `.env.example` has a
local block (active by default) and a cloud block; keep exactly one active. Embeddings
always run on the local Ollama service, so ingestion and retrieval are the same in both
modes; only the answering model changes. Both modes use the same OpenAI-compatible client.

### Cloud (OpenRouter)

1. Create an account at [openrouter.ai](https://openrouter.ai), add credits, and create
   an API key on the [keys page](https://openrouter.ai/settings/keys).
2. In `.env`, comment out the local block and uncomment the cloud block, then set
   `LLM_API_KEY` to the key. The block uses `google/gemini-3.1-flash-lite`: fast and
   inexpensive (about $0.25 per million input tokens and $1.50 per million output tokens
   at the time of writing, i.e. well under a cent per question), and it follows
   "answer only from these passages" instructions reliably. Any OpenRouter model can be
   set in `LLM_MODEL`.
3. `docker compose up -d --build --wait api`. Only the embedding model is pulled.

### Local (Ollama)

The local block is active by default and needs no account or key. `ollama-init` pulls:

| Model | Purpose | Download | Memory when loaded |
|---|---|---|---|
| `qwen3-embedding:0.6b` | embeddings | ~0.6 GB | ~1.2 GB |
| `qwen3:4b-instruct-2507-q4_K_M` | answers | ~2.5 GB | ~3.2 GB |

Both models together use about 4.4 GB of memory once loaded (measured with `ollama ps`),
and about 3.1 GB of disk. The answering model is the non-thinking "instruct" variant, so
answers never contain reasoning output.

Compose runs Ollama on the CPU. Expect about 30-50 s per answer, and longer for the first
question after a start, because a model is loaded on first use (and unloaded after a few
idle minutes). `LLM_TIMEOUT_SECONDS` is 120 in the local block for this reason.

The `ollama` service has safeguards so that memory use stays predictable and a spike
cannot take down the host:

| Setting | Value | Why |
|---|---|---|
| `OLLAMA_NUM_PARALLEL` | 1 | each model serves one request at a time; parallel requests would each need their own context memory |
| `OLLAMA_MAX_LOADED_MODELS` | 2 | exactly the embedding and the answering model stay loaded, nothing else |
| `OLLAMA_CONTEXT_LENGTH` | 4096 | fixes the context memory per model; enough for `RETRIEVAL_TOP_K` passages of the default chunk size plus the answer |
| memory limit | `OLLAMA_MEMORY_LIMIT`, default `6g` | about 1.6 GB above the measured 4.4 GB; if Ollama exceeds it, the kernel kills only the Ollama container, not other processes on the host |

After such a kill the `ollama` container stays stopped (there is no restart policy), and
the API answers `503` until it is started again.

`scripts/e2e.sh` starts its own, separate stack with its own Ollama. Stop the development
stack first (`docker compose down`); two Ollama instances with both models loaded need
about twice the memory.

Compared with the cloud model, the local model is slower and gives less polished answers
to multi-part questions (see [Known limitations](#known-limitations)).

## Question answering design

**Flow.** A LangGraph graph, compiled once at startup: `retrieve -> generate | no_context
-> END` (`query/rag/graph.py`). All per-request data lives in the state of that
invocation. `retrieve` embeds the question (with the query instruction of the embedding
model) and asks pgvector for the `RETRIEVAL_TOP_K` closest chunks; passages with a cosine
similarity below `RETRIEVAL_MIN_SIMILARITY` are dropped. With no passage left, `no_context`
returns a fixed answer and the language model is not called. Otherwise `generate` sends
the numbered passages and the question with a system prompt (`query/rag/prompts.py`,
temperature 0.1, the same for both modes). It asks for plain prose (a numbered list only
for procedures), every fact from the passages that answers the question, commands
generalized with a placeholder such as `<service>` when they come from another service's
runbook, no mention of the passages themselves, and a clear "not covered" instead of a
guess.

**Similarity threshold.** `RETRIEVAL_MIN_SIMILARITY` defaults to 0.5, calibrated on the
sample documents. The top similarity of five answerable questions was 0.64 to 0.81, and
of five unrelated questions 0.20 to 0.41 (the highest being an IT question the documents do
not cover, about single sign-on for an HR portal). Re-check it after changing the
documents or the embedding model.

**Startup.** The service refuses to start if the index was built with another embedding
model or dimension; the logged error names the mismatch, and `--reindex` (see
[Re-indexing](#re-indexing)) rebuilds the index. The database pool, the embedding client
and the language model client are closed on shutdown.

**Language model client.** The official `openai` SDK (`AsyncOpenAI`) with `LLM_BASE_URL`,
`LLM_API_KEY` and `LLM_TIMEOUT_SECONDS`. At most `LLM_MAX_CONCURRENCY` calls run at once.
The SDK retries connection errors, timeouts, 408, 409, 429 and 5xx responses once, so a
timeout is reported after up to twice `LLM_TIMEOUT_SECONDS`.

## Concurrent users and scalability

### Request isolation

**Implemented.** The graph is compiled once at startup; it holds only the adapters and the
retrieval settings. Each `POST /ask` calls `AnswerService.answer()`, which runs
`graph.ainvoke({"question": ...})` and so creates a new state for that invocation; the
question, passages and answer never leave it. The application keeps no module-level mutable
state: `app.state` holds only the objects built at startup (answer service, vector store),
and module-level names are constants. Database access goes
through a `psycopg-pool` connection pool (`DB_POOL_MAX_SIZE`, default 10). Every store call
borrows a connection for one statement or one transaction and returns it, so two requests
never share a connection mid-query, and connections broken by a database restart are
replaced before they are handed out.

**Next steps.** A request ID in every log line, to follow one question through retrieval
and generation when several run at once.

### Parallel execution

**Implemented.** A question can wait in three places: the embedding call (HTTP to Ollama),
the vector search (PostgreSQL) and the answer (HTTP to the language model, seconds to
tens of seconds). All three use async clients (`httpx.AsyncClient`, the async psycopg pool,
`AsyncOpenAI`), so while one request waits for its answer the same process keeps serving
others: health checks, retrieval for new questions and `no_relevant_context` answers. The
API itself does no CPU-heavy work (building a prompt is string formatting), so one uvicorn
process is enough while time is spent waiting on the model. Ingestion is a separate
one-shot process: it is the only writer, it does the heavy work (parsing every file,
embedding every chunk) rarely, and as a job it neither blocks the API's event loop nor
holds connections between runs.

**Next steps.** With `OLLAMA_NUM_PARALLEL=1`, local-mode answers are generated one at a time.
More throughput needs more model capacity (see Bottlenecks), not more API processes.

### Resource limits

**Implemented.**
- At most `LLM_MAX_CONCURRENCY` (default 4) language model calls run at once per API
  process (an `asyncio.Semaphore` in the adapter); further requests wait in the process.
- Timeouts: `LLM_TIMEOUT_SECONDS` per language model attempt, 120 s read and 5 s connect
  for embeddings, 10 s to connect to PostgreSQL or to get a pooled connection.
- Retries are bounded: one SDK retry for the language model, up to three attempts with
  backoff for embeddings.
- Failures are mapped to `503` (unreachable or overloaded) and `504` (timeout), so a client
  or load balancer can tell "try again later" from "too slow".
- Ollama serves one request per model at a time (`OLLAMA_NUM_PARALLEL=1`) and runs under a
  memory limit (`OLLAMA_MEMORY_LIMIT`), see [Running modes](#local-ollama).

**Next steps.**
- In local mode Ollama queues what the semaphore lets through: with four questions at once,
  the last one waits for three answers and can reach `LLM_TIMEOUT_SECONDS`. Setting
  `LLM_MAX_CONCURRENCY` to the backend's real capacity (1 for local Ollama) moves that wait
  into the API.
- A bounded wait for the semaphore, answering `503` with `Retry-After` when it is full, so
  overload fails fast instead of piling up timeouts.
- A restart policy for `ollama`, so a container killed at its memory limit comes back.

### Multiple instances

**Implemented.** The API is stateless: no sessions, no conversation history, no local
files. The index lives in PostgreSQL, so several API containers behind a load balancer
would answer from the same data. Each instance has its own pool and semaphore, so the
totals are per-instance values times the number of instances (for example, 3 instances x
`DB_POOL_MAX_SIZE` 10 = 30 of PostgreSQL's default 100 connections). Ingestion runs once as
a job (`api` starts only after `ingest` completed successfully), and each document is
replaced in one transaction, so the API never sees a half-updated document.

**Next steps.**
- A PostgreSQL advisory lock around an ingestion run, so two runs cannot overlap and race
  on the same documents.
- `--reindex` drops the tables, so the API fails while it runs; building the new index next
  to the old one and switching over would avoid that downtime.
- A concurrency limit shared by all instances (the semaphore is per process), if the model
  provider enforces a global rate limit.

### Bottlenecks

Language model inference is the main limit. On CPU an answer takes about 30-50 s, while
retrieval (one embedding call and one indexed query) takes well under a second.

**Metrics to watch** (today only the logs exist; exposing these is a next step):
- latency per graph node (`retrieve` vs `generate`), to see where time goes;
- time spent waiting for the semaphore, which shows when the model is the queue;
- language model error and timeout rate (`503` / `504` by status code);
- database pool usage (connections in use, requests waiting for one);
- the share of `no_relevant_context` answers, which rises with documentation gaps or a
  threshold set too high.

**Improvement order:**
1. Answer with the cloud model or a GPU host: it removes the largest latency with a
   configuration change only.
2. Bounded semaphore wait with `503`: overload then fails fast and predictably.
3. Stream answers to the client: the user sees text after the first tokens instead of
   after the whole answer.
4. Cache answers to repeated questions, keyed by the normalized question and the index
   content: during an incident many people ask the same thing.
5. Run embeddings on a separate Ollama instance from the answering model: today both
   compete for the same CPU.
6. Add API replicas: worthwhile only once the model backend is no longer the limit,
   because all replicas share it.

## Known limitations

- Answer quality with the local 4B model is limited. It sometimes leaves out a fact that
  is in the passages (for example the 15-minute secondary page when asked about
  acknowledgement time), keeps the example service name in a runbook command instead of
  `<service>`, and answers broad questions with long lists. The cloud model follows the
  prompt more reliably.
- Local answers take about 30-50 s on CPU, and longer after a model was unloaded.
- "Related runbooks" sections are sometimes retrieved: they are short lists of links that
  score well against many questions but add little to an answer, and they can push the
  section with the actual fix out of the top `RETRIEVAL_TOP_K`.
- Retrieval is semantic only. A question that describes a symptom in other words than the
  runbook ("merchants keep getting logged out" for Redis evictions) may not retrieve that
  runbook; the answer then says the documentation does not cover it rather than guessing.
- There is no conversation history and no authentication, by design for this scope: every
  question is answered on its own, and access control is left to where the API is deployed
  (Compose publishes it on `127.0.0.1` only).

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
project name, no published ports) in local mode, so it needs no secrets. It runs `ingest`
on the sample documents in `data/docs` and fails unless it exits with `0` and the `chunks`
table is not empty. It then starts `api`, waits until it is healthy, and checks
(`scripts/e2e_api_checks.py`) that a question the documents answer returns sources and
that an unrelated question returns `no_relevant_context`. Stop the development stack
before running it (`docker compose down`). Ollama models are
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

Each service is its own package and runs as its own process; `ingest` and `query` import
only from `shared`, never from each other.

```
oncall-rag/
├── src/oncall_rag/
│   ├── shared/                 # code used by every service
│   │   ├── config.py           # pydantic-settings: common, ingest and query settings
│   │   ├── logging.py
│   │   ├── errors.py           # application exceptions
│   │   ├── models.py           # Document, Chunk, Passage
│   │   ├── ports.py            # Protocols: Embedder, VectorStore, LanguageModel
│   │   └── adapters/
│   │       ├── embeddings/ollama.py
│   │       ├── llm/openai_compatible.py
│   │       └── vectorstore/pgvector.py
│   ├── ingest/                 # `python -m oncall_rag.ingest`
│   │   ├── __main__.py         # entry point, exception -> exit code mapping
│   │   ├── wiring.py           # composition root: builds adapters from settings
│   │   ├── loaders.py          # .md/.txt reading, front matter, validation
│   │   ├── chunker.py
│   │   └── pipeline.py         # load -> hash -> chunk -> embed -> replace -> prune
│   └── query/                  # `uvicorn oncall_rag.query.main:app`
│       ├── main.py             # app factory and lifespan
│       ├── dependencies.py     # Depends() providers: AnswerService, VectorStore
│       ├── wiring.py           # composition root: builds adapters, the graph, AnswerService
│       ├── routers/            # POST /ask (exception -> HTTPException), GET /health
│       ├── services/           # AnswerService: runs the graph, builds the response
│       ├── schemas/            # request and response bodies
│       └── rag/                # graph state, prompts, retrieve -> generate | no_context
├── tests/
│   ├── fakes.py                # FakeEmbedder, InMemoryVectorStore, FakeLanguageModel
│   ├── fixtures/docs/          # small test documents
│   ├── conftest.py             # fixture document folders
│   ├── ingest/                 # tests mirror src/oncall_rag/: ingest, query, shared
│   ├── query/
│   └── shared/
├── data/docs/                  # sample documents to ingest
├── sql/init.sql                # enables the pgvector extension
├── scripts/e2e.sh              # end-to-end check against the Compose stack
├── scripts/e2e_api_checks.py   # API part of the end-to-end check
├── .github/workflows/ci.yml    # lint, test, build + image scan, end to end
├── Dockerfile
├── docker-compose.yml
├── compose.ci.yml              # override used by scripts/e2e.sh
├── pyproject.toml / uv.lock
├── .pre-commit-config.yaml
├── .env.example
└── README.md
```
