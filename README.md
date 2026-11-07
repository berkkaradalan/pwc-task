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

This starts `db` (PostgreSQL + pgvector) and, in local mode, `ollama`, pulls the two local
models that are not present yet (`ollama-init`, so later runs work offline), then runs the
one-shot `ingest` service. In cloud mode the two Ollama services do not start (see
[Running modes](#running-modes)). `data/docs` is mounted read-only, so after editing documents just run
the command again.

### Locally with uv

Run the database and Ollama from Compose (in cloud mode, `db` alone), and the ingestion code
on the host:

```bash
docker compose up -d db ollama ollama-init
uv sync
uv run python -m oncall_rag.ingest
```

`DATABASE_URL` and `OLLAMA_BASE_URL` in `.env` point at the ports Compose publishes
(`POSTGRES_HOST_PORT`, `OLLAMA_HOST_PORT`); change both together if a port is taken.

### Re-indexing

```bash
uv run python -m oncall_rag.ingest --reindex
# or: docker compose run --rm ingest python -m oncall_rag.ingest --reindex
```

`--reindex` drops and recreates the tables with the current `EMBEDDING_DIM` and indexes
every document again. It is required after changing `EMBEDDING_DIM` (ingestion refuses to
run otherwise) and recommended after changing the chunk settings. A different embedding
model (for example after switching `RUN_MODE`) needs no flag: ingestion rebuilds the index
by itself, see [Storage](#ingestion-design).

An index created by an earlier version must be rebuilt once with `--reindex` when its
schema lacks a column: `documents.embedding_identity` (formerly `embedding_model`) or
`chunks.text_search` (full-text search). Until then, ingestion exits with `1` with the
database error `column "..." does not exist`, so Compose does not start the API either.

The command exits with `0` on success, `1` on fatal errors (database or embedding service
unreachable, documents folder missing, index incompatible with the configuration, an
embedding with the wrong length) and `2` on invalid configuration. Invalid settings are
logged by name and reason, never with their values. A failed embedding call is logged with
the call and its model identity, e.g. `Ingestion failed: embedding call to
qwen/qwen3-embedding-8b@deepinfra: Connection error.`

## Ingestion design

**Flow.** `load -> hash -> skip unchanged -> chunk -> embed -> replace -> prune`
(`ingest/pipeline.py`). The pipeline only depends on the `Embedder` and `DocumentIndex`
ports; `ingest/wiring.py` wires the embedding and pgvector adapters. Ports
(`shared/ports.py`) are `abc.ABC` classes; each adapter and each test fake inherits from
the ports it implements. The vector store is split by consumer: ingestion writes through
`DocumentIndex`, the query service reads through `PassageSearch`, and `PgVectorStore`
implements both.

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
Rollback`. Sections headed "Related runbooks" (any case) are not indexed: they only list
other files, carry nothing to answer from, and outrank the actual fix for short queries.
`.txt` files have no headings. Any section longer than `CHUNK_MAX_CHARS` is
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

**Embeddings.** Qwen3-Embedding: `qwen3-embedding:0.6b` on Ollama in local mode,
`qwen/qwen3-embedding-8b` on OpenRouter in cloud mode (see [Running modes](#running-modes)).
Both are multilingual and work with long inputs. They are instruction-aware, so queries get
an instruction prefix and documents do not; the adapter recognizes both model names, and
this detail stays in the embedding adapter. Every request sends `dimensions=EMBEDDING_DIM`
(1024): the models are trained so that the first values of a vector are a usable embedding
on their own, and both providers return that prefix, normalized again. This keeps the 8B
model (4096 values natively) under pgvector's HNSW limit. In cloud mode every request is
pinned to one OpenRouter provider (`EMBEDDING_PROVIDER`, with fallbacks disabled):
OpenRouter otherwise routes each request to any provider serving the model, and providers
serve different weights (bf16, fp8), so documents and queries could be embedded
differently. On the same input, the providers' vectors differed by up to 0.002 in cosine
similarity, and Nebius returned slightly different vectors on every request; DeepInfra
returned identical ones, so `.env.example` pins it. The adapter uses the same `openai`
SDK as the language model client, against the provider's OpenAI-compatible
`/v1/embeddings` endpoint (float encoding, which on Ollama returns the same vectors as its
native `/api/embed`). Requests are batched (`EMBEDDING_BATCH_SIZE`), use explicit
timeouts, are retried by the SDK (connection errors, timeouts, 408, 409, 429 and 5xx; three
attempts in total), and every returned vector is checked against `EMBEDDING_DIM`.

**Storage.** `documents` and `chunks` (with `ON DELETE CASCADE`) in PostgreSQL, with an
HNSW index using cosine distance, which limits `EMBEDDING_DIM` to 2000 (pgvector rejects
the index above that). The application creates the `vector` extension (once per process,
before the connection pool registers the vector type) and, during ingestion, the tables,
because the vector column dimension comes from `EMBEDDING_DIM`. Each document records its embedding identity: the
model, plus the provider when one is pinned (for example
`qwen/qwen3-embedding-8b@deepinfra`), so vectors from different models or providers are
never mixed. If the stored value differs from the configured one, ingestion logs one warning
naming both and rebuilds the index; with the same value nothing is rebuilt. If the stored dimension differs from
`EMBEDDING_DIM`, ingestion stops and asks for `--reindex`.

For full-text search, `chunks` has a generated `text_search` column (`to_tsvector('english',
text)`) with a GIN index, created with the tables.

## Running the API

The question-answering service (`query`) answers questions from the indexed documents.

```bash
docker compose up -d --build --wait api
```

This runs ingestion first (`api` starts only after `ingest` completed successfully), then
serves the API on `127.0.0.1:${API_HOST_PORT}` (default 8000). The container is healthy
once `GET /health` answers `200`. To run the API on the host instead, start
`docker compose up -d db ollama ollama-init`, run ingestion (see
[Running ingestion](#running-ingestion)), and run
`uv run uvicorn oncall_rag.query.main:app --no-access-log`; `OLLAMA_BASE_URL` in `.env`
already points at the host port.

### API

`POST /ask` takes `{"question": "..."}` (surrounding whitespace is stripped; 1 to 1000
characters) and an optional `history` of earlier turns, and returns the answer, a status
(`answered` or `no_relevant_context`), the `search_query` used for retrieval, and the
passages it was based on. Sources come from retrieval, never from the model output.

A question the documentation covers:

```bash
curl -s localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question": "How do I mitigate a payments worker queue backlog?"}'
```

```json
{
  "answer": "1. Scale up worker pods if the workers are healthy but processing is lagging: ...",
  "status": "answered",
  "search_query": "How do I mitigate a payments worker queue backlog?",
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
{"answer": "No relevant documentation was found for this question.", "status": "no_relevant_context", "search_query": "What is the capital of France?", "sources": []}
```

A follow-up question sends the earlier turns as `history`, oldest first. Each message has
a `role` (`user` or `assistant`) and a `content` of 1 to 4000 characters; at most 10
messages. `search_query` shows the standalone question that was searched for:

```bash
curl -s localhost:8000/ask -H 'Content-Type: application/json' -d '{
  "question": "Why not?",
  "history": [
    {"role": "user", "content": "Should I run FLUSHALL to fix Redis memory pressure?"},
    {"role": "assistant", "content": "No. Running `FLUSHALL` is not recommended to fix Redis memory pressure. ..."}
  ]
}'
```

```json
{
  "answer": "Running `FLUSHALL` is not recommended because it deletes every session and every idempotency key. ...",
  "status": "answered",
  "search_query": "Why not run FLUSHALL to fix Redis memory pressure?",
  "sources": [
    {
      "source": "redis-memory-pressure.md",
      "title": "Redis Memory Pressure",
      "heading_path": "Mitigation",
      "score": 0.786,
      "excerpt": "Do not run `FLUSHALL` as a reflex. It deletes every session and every idempotency key, ..."
    }
  ]
}
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

Errors use FastAPI's default bodies. Adapters let the `openai` and `psycopg` exceptions
propagate unchanged; the `/ask` route translates them into `HTTPException` with a short,
fixed `detail`. The exception text, which may name internal hosts, is only logged. The
adapters add a note to the exception without changing it, so the warning also names the
failed call and its model, e.g. `Answering failed: embedding call to
qwen/qwen3-embedding-8b@deepinfra: Error code: 404 - ...`.

| Status | Body | Cause |
|---|---|---|
| 422 | `{"detail": [...]}` | the request body is invalid |
| 502 | `{"detail": "..."}` | the language model or embedding service rejected the request (e.g. bad API key, unknown model), or its answer is unusable (empty answer, wrong vector length) |
| 503 | `{"detail": "..."}` | the language model or embedding service is unreachable, overloaded (429) or failing (5xx), OpenRouter has no provider available for the model (404, for example the pinned embedding provider is down), or the database is unreachable |
| 504 | `{"detail": "..."}` | the language model or embedding service did not answer in time (`LLM_TIMEOUT_SECONDS` for the language model) |
| 500 | `{"detail": "Internal server error", "request_id": "..."}` | anything unexpected; the traceback is only in the logs, under the same request ID |

## Running modes

`RUN_MODE` in `.env` selects where both models run: `local` (the default: Ollama, no account
or key) or `cloud` (OpenRouter). Switching is a configuration change only. `shared/config.py`
is the only place that turns the mode into an endpoint, a key and a model name; the
adapters use the same OpenAI-compatible client in both modes. All four models are from the
open-weights Qwen3 family, and their names are set in `.env`:

| | Local (Ollama) | Cloud (OpenRouter) |
|---|---|---|
| Embeddings | `qwen3-embedding:0.6b` (`LOCAL_EMBEDDING_MODEL`) | `qwen/qwen3-embedding-8b` (`CLOUD_EMBEDDING_MODEL`) |
| Answers | `qwen3:4b-instruct-2507-q4_K_M` (`LOCAL_LLM_MODEL`) | `qwen/qwen3-30b-a3b-instruct-2507` (`CLOUD_LLM_MODEL`) |

Why these sizes:
- `qwen3-embedding:0.6b`: small enough for a laptop CPU (about 1.2 GB loaded) and natively
  1024-dimensional.
- `qwen/qwen3-embedding-8b`: the largest model of the same family, with the same query
  instruction; its 4096 values are truncated to `EMBEDDING_DIM` by the provider.
- `qwen3:4b-instruct-2507-q4_K_M`: a 4-bit 4B model fits next to the embedding model in about
  4.4 GB of memory.
- `qwen/qwen3-30b-a3b-instruct-2507`: a mixture-of-experts model with 3.3B active parameters
  per token, so it answers quickly; enough to answer from `RETRIEVAL_TOP_K` passages; about
  $0.10 per million input and $0.30 per million output tokens on OpenRouter at the time of
  writing; and the same 2507 instruct generation as the local model, so the prompts behave
  the same.

Both answering models are non-thinking "instruct" variants, so answers never contain
reasoning output.

Switching modes changes the embedding model. The next ingestion rebuilds the index (see
[Storage](#ingestion-design)), and until then the API refuses to start.
`docker compose up -d --build --wait api` runs ingestion first, so it does both.

`ollama` and `ollama-init` are in the Compose profile `local`. `COMPOSE_PROFILES=${RUN_MODE}`
in `.env` turns it on only in local mode, so `RUN_MODE` stays the only switch; `ingest`
depends on `ollama-init` with `required: false`, so cloud mode starts without it. A `.env`
copied from an earlier `.env.example` lacks that line: cloud mode works without it, but
local mode then starts no Ollama and ingestion exits with `1`, so add the line. After a
local run, stop the stack with `docker compose --profile '*' down`: a plain
`docker compose down` while `RUN_MODE` is `cloud` leaves the `ollama` container running.

### Cloud (OpenRouter)

1. Create an account at [openrouter.ai](https://openrouter.ai), add credits, and create
   an API key on the [keys page](https://openrouter.ai/settings/keys).
2. In `.env`, set `RUN_MODE=cloud` and `OPENROUTER_API_KEY` to the key, and keep
   `EMBEDDING_PROVIDER` (see Embeddings in [Ingestion design](#ingestion-design)). Cloud
   mode without either is rejected as invalid configuration at startup. If the pinned
   provider is down, requests fail (`503`, ingestion exits with `1`) instead of silently
   switching to another provider; set another one and run ingestion again, which then
   rebuilds the index.
3. `docker compose up -d --build --wait api`. This starts only `db`, `ingest` and `api`.

### Local (Ollama)

Local mode is the default and needs no account or key. `ollama-init` pulls:

| Model | Purpose | Download | Memory when loaded |
|---|---|---|---|
| `qwen3-embedding:0.6b` | embeddings | ~0.6 GB | ~1.2 GB |
| `qwen3:4b-instruct-2507-q4_K_M` | answers | ~2.5 GB | ~3.2 GB |

Both models together use about 4.4 GB of memory once loaded (measured with `ollama ps`),
and about 3.1 GB of disk.

If `ollama-init` fails (for example, a first pull without network), Compose only warns
(`optional dependency "ollama-init" didn't complete successfully`) and still starts
`ingest`, which then exits with `1` on the missing model, so the API does not start.

Compose runs Ollama on the CPU. Expect about 30-50 s per answer, and longer for the first
question after a start, because a model is loaded on first use (and unloaded after a few
idle minutes). `LLM_TIMEOUT_SECONDS` is 120 in `.env.example` for this reason.

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

### Cloud vs local

Measured on the sample documents, one request at a time from the same machine: in cloud
mode 15 questions and one follow-up, in local mode one question (should `FLUSHALL` fix
Redis memory pressure).

| | Local | Cloud |
|---|---|---|
| Answer latency | 45 s for the measured question; 30-50 s typical on CPU | 1.2-15.2 s, median 7.4 s over 14 answers; 13.9 s for the same question |
| Fixed "no relevant context" answer | well under a second (local embedding call) | 3.5 and 8.8 s: the embedding call goes over the network and its latency varies |
| First ingestion of the 12 documents | 65 s | 80 s |
| Top similarity, answerable vs unrelated | 0.64-0.81 vs 0.20-0.41 | 0.61-0.91 vs 0.27-0.37 |

Quality differences seen in these runs:
- Both answered the `FLUSHALL` question correctly from the same passage. The cloud answer
  also listed the mitigation steps in order; the local answer summarized them in one sentence.
- The cloud model answered the factual questions (acknowledgement time, latency threshold,
  postmortem deadline, `maxmemory`, pool size, severity of a queue backlog) in one or two
  sentences each, and condensed the "Why not?" follow-up into a full question that
  retrieved the Redis runbook again.
- Some prompt rules are missed in both modes: the cloud model also left out the 15-minute
  secondary page and kept `payments-worker` in the rollback commands instead of `<service>`
  (see [Known limitations](#known-limitations)).
- The cloud embedder did not retrieve the replica-lag runbook for "a customer reloads after
  paying and still sees the payment as pending", and the answer blamed a card processor
  outage from the passages it got.

## Question answering design

**Flow.** A LangGraph graph, compiled once at startup: `[condense_question ->] retrieve ->
generate | no_context -> END` (`query/rag/graph.py`). All per-request data lives in the
state of that invocation. A conditional edge from the start goes straight to `retrieve`
when there is no history, and to `condense_question` otherwise (see Conversation history).
`retrieve` embeds the search query (the question itself when there is no history) with
the query instruction of the embedding model and runs one SQL query for two candidate
lists of 20 chunks each: the chunks closest to that vector, and the chunks whose text best
matches the query's words (PostgreSQL full-text search with English stemming, any of the
words, ranked by `ts_rank`). The two lists are merged with Reciprocal Rank Fusion
(`query/rag/fusion.py`, k = 60: each chunk scores the sum of 1 / (60 + its rank) over the
lists that contain it), and the first `RETRIEVAL_TOP_K` are kept. Full text finds runbooks
that describe a symptom in the question's own words ("charged twice", "logged out") when
the vector ranks them too low. Only the vector list decides whether anything is relevant:
if none of its chunks reaches a cosine similarity of `RETRIEVAL_MIN_SIMILARITY`,
`no_context` returns a fixed answer and the language model is not called, because full
text also matches common words of unrelated questions. Once that gate is open, every
fused passage is kept, including full-text matches with a lower similarity; each source
still shows its cosine similarity as `score`. Otherwise `generate` sends the numbered passages and the question with a
system prompt (`query/rag/prompts.py`, temperature 0.1, the same for both modes). It asks for plain prose (a numbered list only
for procedures), every fact from the passages that answers the question, commands
generalized with a placeholder such as `<service>` when they come from another service's
runbook, no mention of the passages themselves, and a clear "not covered" instead of a
guess.

**Conversation history.** The API stays stateless: the client sends the earlier turns with
each request and the server stores nothing. A follow-up such as "Why not?" cannot be
searched as is, so `condense_question` first asks the language model to rewrite it as a
standalone question from the history (a short prompt in `query/rag/prompts.py`). The prompt
asks for a rewrite whenever the question refers to the conversation (it, that, this, a
leading "and" or "also", or words left out), and allows it unchanged only otherwise; each
rewrite is logged at INFO with the request ID. `retrieve`
embeds that rewrite, never the raw follow-up, and the response returns it as
`search_query`. `generate` then sends the history as earlier chat messages, followed by the
passages and the original question. Without history no extra call is made. Clients can
only send `user` and `assistant` messages: a `system` role is rejected with `422`, so a
client cannot add instructions that compete with the system prompt. A request carries at
most 10 messages of up to 4000 characters each. Before each language model call only the
most recent messages whose combined length is at most 4000 characters are kept, and older
ones are dropped. The local model has a 4096-token context (`OLLAMA_CONTEXT_LENGTH`) that
must also hold the system prompt, the passages and the answer. Ollama silently truncates
the start of an oversized prompt, which would drop the system prompt; the limit prevents
that. Condensation uses the same adapter and concurrency limit as `generate`, so its
failures map to the same HTTP statuses.

**Similarity threshold.** `RETRIEVAL_MIN_SIMILARITY` defaults to 0.5, calibrated on the
sample documents, and applies in both modes. With the local embedding model, the top
similarity of five answerable questions was 0.64 to 0.81, and of five unrelated questions
0.20 to 0.41 (the highest being an IT question the documents do not cover, about single
sign-on for an HR portal). With the cloud embedding model (pinned to DeepInfra), fifteen
answerable questions, including symptom and Turkish questions, scored 0.56 to 0.91, and four
unrelated ones 0.21 to 0.45 (the highest being TLS certificate renewal for api-gateway, a
service the documents name). The local range was measured on fewer questions and has not
been re-measured since. A question about a component the documents only mention
(`rabbitmq-0` going down) scored 0.58, passed, and was answered as not covered. The gate uses
only these vector scores; full-text matches never open it. Re-check the threshold after
changing the documents or an embedding model.

**Startup.** The service refuses to start if the index has not been built yet (run
ingestion first; only ingestion creates the schema) or was built with another embedding
model, provider or dimension; the logged error names the problem. Running ingestion rebuilds
an index built with another model or provider; a different dimension needs `--reindex` (see
[Re-indexing](#re-indexing)). The database pool, the embedding client
and the language model client are closed on shutdown.

**Language model client.** The official `openai` SDK (`AsyncOpenAI`) with the endpoint, key
and model resolved from `RUN_MODE`, and `LLM_TIMEOUT_SECONDS`. At most `LLM_MAX_CONCURRENCY` calls run at once.
The SDK retries connection errors, timeouts, 408, 409, 429 and 5xx responses once, so a
timeout is reported after up to twice `LLM_TIMEOUT_SECONDS`.

## Concurrent users and scalability

### Request isolation

**Implemented.** The graph is compiled once at startup; it holds only the adapters and the
retrieval settings. Each `POST /ask` calls `AnswerService.answer()`, which runs
`graph.ainvoke()` with the question and the history from the request and so creates a new
state for that invocation; the question, history, passages and answer never leave it. The application keeps no module-level mutable
state: `app.state` holds only the objects built at startup (answer service, passage search),
and module-level names are constants. Database access goes
through a `psycopg-pool` connection pool (`DB_POOL_MAX_SIZE`, default 10). Every store call
borrows a connection for one statement or one transaction and returns it, so two requests
never share a connection mid-query, and connections broken by a database restart are
replaced before they are handed out.

Every request gets an ID (`asgi-correlation-id`, `query/core/middleware.py`): a valid UUID
sent in `X-Request-ID` is reused, otherwise a new one is generated, and every response
returns it in `X-Request-ID`. The ID is kept in a context variable, so concurrent requests
never see each other's ID, and a logging filter adds it to every API log line, uvicorn's
included, to follow one question through retrieval and generation when several run at
once. One access log line per request (method, path, status, duration in ms) replaces
uvicorn's access log, which is turned off with `--no-access-log`. A `500` body carries the
same `request_id`, so a user can report it.

### Parallel execution

**Implemented.** A question can wait in three places: the embedding call (HTTP to Ollama or
OpenRouter),
the vector search (PostgreSQL) and the answer (HTTP to the language model, seconds to
tens of seconds). All three use async clients (`AsyncOpenAI` for both models, the async
psycopg pool), so while one request waits for its answer the same process keeps serving
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

**Implemented.** The API is stateless: no sessions, no local files, and conversation
history is sent by the client with each request and never stored. The index lives in PostgreSQL, so several API containers behind a load balancer
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
  `<service>`, and answers broad questions with long lists. The cloud model gave the first
  two of these answers the same way (see [Cloud vs local](#cloud-vs-local)).
- Local answers take about 30-50 s on CPU, and longer after a model was unloaded.
- Full-text search lifts runbooks that share the question's words, but a symptom phrased
  differently from the runbook can still miss the top `RETRIEVAL_TOP_K`. In cloud mode, a
  payment still "pending" after a reload (replica lag) ranks fifth after fusion, and the
  answer blamed a card processor outage. The answer usually says the documentation does
  not cover a question it cannot find, but it can also answer from the wrong runbook.
- Full-text search uses English stemming, so it does not help non-English questions: the
  same "pending" question in Turkish matches no chunk by text and relies on vector search
  alone.
- A follow-up question costs one extra language model call to condense it. The rewrite is
  a single short question, so on CPU it added about 2 s to a 42 s answer in one measured
  follow-up, not a second full answer.
- Small models may condense poorly: a rewrite that drops the subject retrieves the wrong
  passages. `search_query` in the response shows what was searched for.
- There is no authentication, by design for this scope: access control is left to where
  the API is deployed (Compose publishes it on `127.0.0.1` only).

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
project name, no published ports, and `RUN_MODE=local` with the `local` profile on, whatever
`.env` says), so it needs no secrets. It runs `ingest`
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
│   │   ├── config.py           # pydantic-settings; resolves RUN_MODE to endpoints and models
│   │   ├── logging.py
│   │   ├── errors.py           # application exceptions
│   │   ├── models.py           # Document, Chunk, Passage, ChatMessage
│   │   ├── ports.py            # ABCs: Embedder, DocumentIndex, PassageSearch, LanguageModel
│   │   └── adapters/
│   │       ├── embeddings/openai_compatible.py
│   │       ├── llm/openai_compatible.py
│   │       └── vectorstore/pgvector.py
│   ├── ingest/                 # `python -m oncall_rag.ingest`
│   │   ├── __main__.py         # entry point, exception -> exit code mapping
│   │   ├── wiring.py           # composition root: builds adapters from settings
│   │   ├── loaders.py          # .md/.txt reading, front matter, validation
│   │   ├── chunker.py
│   │   └── pipeline.py         # load -> hash -> chunk -> embed -> replace -> prune
│   └── query/                  # `uvicorn oncall_rag.query.main:app --no-access-log`
│       ├── main.py             # app factory and lifespan
│       ├── core/
│       │   ├── middleware.py   # request ID, access log, JSON 500, API log format
│       │   ├── dependencies.py # Depends() providers: AnswerService, PassageSearch
│       │   └── wiring.py       # composition root: builds adapters, the graph, AnswerService
│       ├── routers/            # POST /ask (exception -> HTTPException), GET /health
│       ├── services/           # AnswerService: runs the graph, builds the response
│       ├── schemas/            # request and response bodies
│       └── rag/                # graph state, prompts, rank fusion, [condense ->] retrieve -> generate
├── tests/
│   ├── fakes.py                # FakeEmbedder, InMemoryVectorStore, FakeLanguageModel
│   ├── fixtures/docs/          # small test documents
│   ├── conftest.py             # fixture document folders
│   ├── ingest/                 # tests mirror src/oncall_rag/: ingest, query, shared
│   ├── query/
│   └── shared/
├── data/docs/                  # sample documents to ingest
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
