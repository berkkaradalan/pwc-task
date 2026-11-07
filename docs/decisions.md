# Design decisions

Each section covers one decision: its context, what was decided, the alternatives, and the
trade-off. The measurements referred to here are in [evaluation.md](evaluation.md), and
[architecture.md](architecture.md) shows how the pieces fit together.

## pgvector in PostgreSQL

**Context.** The index holds documents, their chunks and a vector per chunk. Since hybrid
retrieval was added, it also holds a full-text index. The sample corpus is small: 12
runbooks and 79 chunks.

**Decision.** Everything is stored in PostgreSQL with the pgvector extension, using an
HNSW index with cosine distance. The reasons:

- Documents, chunks, vectors and the full-text index live in one database.
- One SQL query returns both candidate lists, vector and full text.
- A document's chunks are replaced in one transaction, so the API never sees a
  half-updated document.
- Several API instances share one index.
- pgvector's HNSW index is enough far beyond this scale. PostgreSQL is a component most
  teams already run and back up, so it adds no new infrastructure.

**Alternatives.** A dedicated vector database would mean one more service to run,
full-text search in a separate system, and no transaction spanning a document's metadata
and its vectors. It would be worth it at much larger scale, or with needs PostgreSQL cannot
meet, but not here.

**Trade-off.** The HNSW index limits vectors to 2000 dimensions, so the 4096-dimensional
cloud model is truncated to `EMBEDDING_DIM` (1024). The vector search also returns at most
`hnsw.ef_search` rows (40 by default), which caps the candidates per method.

## Chunking and the embedding text prefix

**Context.** Runbooks are Markdown with headings, code blocks and a "Related runbooks"
list. A short section such as "Mitigation" means little without its document.

**Decision.**
- Markdown is split on H1–H3 headings with langchain's
  `ExperimentalMarkdownSyntaxTextSplitter`. It keeps code fences intact, so a `#` inside
  code is not taken for a heading. Blocks under the same heading are joined again, so a
  code sample stays with the text that explains it.
- Sections longer than `CHUNK_MAX_CHARS` (1500) are split on paragraphs, then lines, then
  sentences, with up to `CHUNK_OVERLAP_CHARS` (200) of overlap.
- "Related runbooks" sections are not indexed. They only list other files, carry nothing to
  answer from, and outranked the actual fix for short queries.
- The embedder receives `<title> > <heading path>` followed by the chunk text, so short
  chunks keep their context. The raw text is stored separately for citations. A first
  heading that repeats the title is not repeated.
- Queries get the Qwen3-Embedding instruction prefix and documents do not, because the
  model is instruction-aware.

**Alternatives.** One alternative is fixed-size chunks that cut across headings. Another is
embedding the raw chunk text only, which loses the document and section a short chunk
belongs to.

**Trade-off.** The content hash covers the file only, so changing the chunk settings
needs `--reindex`.

## Hybrid retrieval with RRF and a vector-only gate

**Context.** With vector search alone, the expected runbook for the symptom questions Q6
and Q7 ranked sixth to tenth in both modes, outside the top 4.

**Decision.**
- One SQL query returns the 20 chunks nearest to the query vector and the 20 chunks that
  best match its words, using PostgreSQL full-text search with the `english` configuration.
- Full-text search matches any of the words (OR) rather than all of them, so a chunk that
  shares only some of the question's words still matches. `ts_rank` normalization 1 keeps
  long chunks from winning on length alone.
- The two lists are merged with Reciprocal Rank Fusion, with k = 60, the constant from the
  original RRF paper. It damps the lead of the very top ranks, so a chunk ranked well by
  both lists beats one ranked first by only one of them. The first `RETRIEVAL_TOP_K` are
  kept.
- Only the vector list opens the relevance gate. If no vector candidate reaches
  `RETRIEVAL_MIN_SIMILARITY`, the answer is `no_relevant_context`. Full text also matches
  unrelated questions, as o1 did with 20 full-text matches, so it adds candidates but never
  opens the gate.
- Every source shows its cosine similarity as `score`, including the ones found by full
  text.

**Alternatives.** Vector search alone missed Q6, Q7, s1 and s2. A larger
`RETRIEVAL_TOP_K` and a weighted fusion were not tried.

**Trade-off.** Hits rose from 14/20 to 17/20 with no regressions. Q7 still ranks fifth,
however, and non-English questions get no help from full text. Once the gate is open,
full-text passages with a low similarity are kept as well.

## No reranker

**Context.** A cross-encoder reranker could reorder the fused candidates.

**Decision.** The project uses no reranker.

**Alternatives.** Ollama has no rerank API, and only community uploads emulate one. On
OpenRouter, Qwen3-Reranker-8B had a single provider at about 85% availability when checked.
A reranker would therefore have worked in only one mode, or depended on one provider.

**Trade-off.** Ranking quality rests on the embedding model and full-text search alone.

## Pinning the embedding provider

**Context.** OpenRouter routes each request to any provider that serves the model, and the
providers of `qwen/qwen3-embedding-8b` serve different weights (bf16, fp8). Documents and
queries could therefore be embedded by different weights.

**Decision.** In cloud mode `EMBEDDING_PROVIDER` is required, and every embedding request is
pinned to that provider with fallbacks disabled. `.env.example` pins DeepInfra, which
returned bit-identical vectors in sequential runs. Nebius drifted even when pinned.

**Alternatives.** With unpinned routing, scores changed between runs, and ingestion took
80–90 s instead of 20 s.

**Trade-off.** If the pinned provider is down, requests fail instead of switching
provider: the API answers `503` and ingestion exits with `1`. Switching provider is a
configuration change followed by an automatic rebuild, described in the next section. The
language model is not pinned.

## Embedding identity and automatic rebuild

**Context.** Vectors from different models are not comparable, and neither are vectors from
the same model served with other weights.

**Decision.** Every document stores its embedding identity: the model, plus the provider
when one is pinned, for example `qwen/qwen3-embedding-8b@deepinfra`. When ingestion finds
another identity in the index, it logs one warning naming both and rebuilds the index. The
API refuses to start if the index holds another identity or dimension.

**Alternatives.** Ingestion could refuse to run until the operator runs `--reindex`, which
adds a manual step after every mode switch.

**Trade-off.** A mode switch re-embeds every document, which takes about 20 s in cloud mode
and 65 s in local mode for the samples. A changed `EMBEDDING_DIM` still needs `--reindex`,
because the column type changes.

## Reindex instead of migrations

**Context.** The schema changed twice during development: a column was renamed, and a
full-text column was added.

**Decision.** There is no migration code. The schema is created with
`CREATE TABLE IF NOT EXISTS`. After a schema change, one run of
`python -m oncall_rag.ingest --reindex` drops and recreates the tables and indexes every
document again.

**Alternatives.** A migration tool with migration scripts.

**Trade-off.** The index is derived data that can be rebuilt from `DOCS_DIR` in about a
minute, so migration code would cost more than it saves. The cost is a full rebuild per
schema change, and the API cannot answer while `--reindex` has dropped the tables.

## Stateless, client-sent history

**Context.** Follow-up questions such as "Why not?" need the earlier turns.

**Decision.** The client sends the history with each request, and the server stores
nothing. A request may carry at most 10 messages of up to 4000 characters each, with the
roles `user` and `assistant` only. Before each language model call, only the most recent
messages totalling at most 4000 characters are kept.

**Alternatives.** Server-side sessions would keep state per conversation, and every API
instance would need to share it.

**Trade-off.** Requests grow with the conversation, and older turns are dropped. The
4000-character limit exists because the local model's context window is 4096 tokens, and
that window must also hold the system prompt, the passages and the answer. When a prompt is
longer, Ollama silently drops its start, which is where the system prompt is. A `system`
role is rejected with `422`, so a client cannot add instructions that compete with the
system prompt.

## Condensing follow-ups and the fallback

**Context.** A follow-up such as "Why not?" cannot be searched as it is.

**Decision.**
- When there is history, `condense_question` asks the language model to rewrite the
  question as a standalone one. The prompt allows a standalone question to come back
  unchanged. The rewrite becomes the `search_query`.
- A small model does not always rewrite, whatever the prompt says. When the rewrite equals
  the question apart from case and whitespace, and there is an earlier user question,
  `retrieve_follow_up` searches twice. It searches with the question alone and with the
  previous question followed by it, and keeps the search whose closest chunk is more
  similar.

**Alternatives.**
- A stricter condense prompt made the local model rewrite "Why not?" in 0 of 5 runs, against
  5 of 5 with the current one.
- Falling back only when the question alone finds nothing does not work. "Why not?" alone
  passed the gate at 0.514 on an unrelated runbook, so finding something does not show that
  a question was standalone.
- Always prepending the previous question would search a standalone question on a new topic
  mixed with the old one.

**Trade-off.** Each follow-up costs one extra language model call. When the model did not
rewrite, it also costs one extra embedding call and search. Only the previous user question
is added, not older turns, and a rewrite that drops the subject is not detected.

## Local and cloud modes, and Compose profiles

**Context.** Local models need no account or key, but they answer slowly on CPU. A hosted
model answers in seconds, but needs a key and credits.

**Decision.** `RUN_MODE` selects where both models run. In `local` mode, the default, they
run on Ollama on the CPU, with no account or key. In `cloud` mode they run on OpenRouter.
`shared/config.py` is the only place that turns the mode into an endpoint, a key and a
model, and both adapters use the same OpenAI-compatible client.

All models are open-weights Qwen3. Both answering models are non-thinking instruct variants
of the same 2507 generation, so the prompts behave the same and the answers never contain
reasoning output. The sizes were chosen as follows:

- `qwen3-embedding:0.6b` is small enough for a laptop CPU, at about 1.2 GB loaded, and is
  natively 1024-dimensional.
- `qwen/qwen3-embedding-8b` is the largest model of the same family and uses the same query
  instruction. The provider truncates its 4096 values to `EMBEDDING_DIM`.
- `qwen3:4b-instruct-2507-q4_K_M` is a 4-bit 4B model that fits next to the embedding model
  in about 4.4 GB of memory.
- `qwen/qwen3-30b-a3b-instruct-2507` is a mixture-of-experts model with 3.3B active
  parameters per token, so it answers quickly. When it was chosen, it cost about $0.10 per
  million input tokens and $0.30 per million output tokens on OpenRouter.

In Compose, `ollama` and `ollama-init` are in the profile `local`, and `.env.example` sets
`COMPOSE_PROFILES=${RUN_MODE}`. `RUN_MODE` therefore stays the only switch, and cloud mode
starts no Ollama. `ingest` depends on `ollama-init` with `required: false`, so it can start
without it.

**Alternatives.** A second, independent setting for the profile could silently disagree
with `RUN_MODE`.

**Trade-off.**
- Local answers take 30–50 s on CPU, and the 4B model follows the prompt less reliably.
  Cloud answers take about 7 s, but need a key and credits.
- With `required: false`, a failed `ollama-init` no longer stops `ingest`. Compose only
  warns, and ingestion then fails on the missing model.
- After switching from local to cloud, a plain `docker compose down` leaves `ollama`
  running, because its profile is off. `docker compose --profile '*' down` stops it.

## Exception notes instead of wrapping

**Context.** A request makes two kinds of provider calls, embedding and language model,
through the same `openai` SDK. An `openai` error alone therefore does not say which call
failed.

**Decision.** Adapters let `openai` and `psycopg` exceptions propagate unchanged. They only
add a note that names the call: `embedding call to <model@provider>` or
`LLM call to <model>`. The `/ask` route and the ingestion entry point translate the
exception types into HTTP status codes and exit codes, and log the notes with the error
text. The application defines its own exceptions only for rules that no library expresses:
`ReindexRequiredError`, and `ProviderResponseError` for an answer that arrived but is
unusable.

**Alternatives.** Each library error could be wrapped in an application exception type.

**Trade-off.** The boundaries depend on the `openai` and `psycopg` exception types, and the
notes appear only where the log line uses `failure_summary`.
