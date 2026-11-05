# pwc-task
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
├── data/docs/                  # documents to ingest
├── sql/init.sql                # enables the pgvector extension
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml / uv.lock
├── .env.example
└── README.md
```
