# Evaluation

All measurements use the 12 sample runbooks in `data/docs`, which make 79 chunks. Requests
were sent one at a time unless stated otherwise. Each result names the mode it comes from:

- **cloud**: OpenRouter, with `qwen/qwen3-embedding-8b` pinned to DeepInfra and
  `qwen/qwen3-30b-a3b-instruct-2507`.
- **local**: Ollama on CPU, with `qwen3-embedding:0.6b` and `qwen3:4b-instruct-2507-q4_K_M`.

These are small, hand-run checks, not a benchmark. Re-run them after changing the
documents, a model or `RETRIEVAL_MIN_SIMILARITY`.

## Question set

| ID | Kind | Question | Expected runbook |
|---|---|---|---|
| Q1 | answerable | How quickly must the primary on-call acknowledge a page? | `oncall-escalation-policy.md` |
| Q2 | answerable | What is the alert threshold for checkout-api latency? | `checkout-api-high-latency.md` |
| Q3 | answerable | When is a postmortem required and by when? | `postmortem-process.md` |
| Q4 | answerable | What is the maxmemory of redis-cache? | `redis-memory-pressure.md` |
| Q5 | answerable | How do I roll back a bad deploy if Argo CD is unavailable? | `deploy-rollback-procedure.md` |
| Q6 | answerable (symptom) | Merchants keep getting logged out during checkout. What could be wrong? | `redis-memory-pressure.md` |
| Q7 | answerable (symptom) | A customer reloads after paying and still sees the payment as pending. Why? | `postgres-replica-lag.md` |
| Q8 | answerable | Our payment provider is down. What should we do? | `card-processor-outage.md` |
| Q9 | answerable | Should I run FLUSHALL to fix Redis memory pressure? | `redis-memory-pressure.md` |
| Q10 | answerable | PgBouncer alert fired together with checkout 5xx. Likely culprit and action? | `postgres-connection-pool-exhausted.md` |
| Q11 | answerable | Is a payments.charge queue backlog SEV1 or SEV2? | `payments-worker-queue-backlog.md`, `severity-levels.md` |
| Q12 | answerable | Can I increase the PgBouncer pool size during an incident? | `postgres-connection-pool-exhausted.md` |
| Q13 | unrelated | What is the capital of France? | none |
| Q14 | unrelated | How do I configure Kafka consumer groups? | none |
| Q15 | missing runbook | What should I do when rabbitmq-0 goes down? | none; the runbooks only mention it |
| s1 | symptom | Merchants say they were charged twice for the same checkout attempt. What could cause this? | `redis-memory-pressure.md` |
| s2 | symptom | Reconciliation jobs report ledger mismatches but no checkout failed. How serious is this? | `postgres-replica-lag.md` |
| t1 | non-English | Q7 asked in Turkish: "The customer refreshes the page right after paying and still sees the payment as pending. What could be the cause?" | `postgres-replica-lag.md` |
| o1 | unrelated | How do I renew the TLS certificate on api-gateway? | none |
| o2 | unrelated | How do I bake sourdough bread? | none |

A question counts as a hit when the expected runbook is among the 4 sources returned
(`RETRIEVAL_TOP_K`).

## Retrieval before hybrid search

With vector search alone, the expected runbook for the two symptom questions ranked as
follows:

| Question | Cloud | Local |
|---|---|---|
| Q6 (`redis-memory-pressure.md`) | 6 | 10 |
| Q7 (`postgres-replica-lag.md`) | 8–10 | 8 |

Neither reached the top 4 in either mode, which led to adding full-text search
([decision](decisions.md#hybrid-retrieval-with-rrf-and-a-vector-only-gate)).

## Hybrid retrieval (cloud)

Across the 20 questions, hits rose from 14/20 before to 17/20 after. There were no
regressions: every question that was a hit before stayed a hit.

| Question | Vector-only rank | Fused rank | Full-text rank |
|---|---|---|---|
| Q6 | 6 | **3** | 3 |
| Q7 | 8 | 5 | 1 |
| s1 | 7 | **3** | 1 |
| s2 | 7 | **1** | 1 |
| t1 (Turkish) | 8 | 8 | no match |

Q6, s1 and s2 moved into the top 4. Q7 improved but stays fifth, so its answer is still
built from the wrong runbooks: it blamed the queue backlog and a card processor outage. t1
is unchanged, because full-text search uses the English configuration and matches no chunk
for a Turkish question.

Q13, Q14, o1 and o2 still returned `no_relevant_context`, although o1 had 20 full-text
matches and Q14 had 15. This is why full text must not open the relevance gate.

## Similarity threshold calibration (cloud, pinned)

The top vector similarity per question, measured after hybrid search was added:

| Group | Range |
|---|---|
| Answerable (Q1–Q12, s1, s2, t1) | 0.56 (s2) – 0.91 (Q11) |
| Unrelated (Q13, Q14, o1, o2) | 0.21 (o2) – 0.45 (o1) |
| Missing runbook (Q15) | 0.58 |

The default `RETRIEVAL_MIN_SIMILARITY` of 0.5 lies in the gap, with a margin of about 0.05
on each side: o1 below, s2 above. Q15 passes the gate, and the model answers that the
documentation does not cover it.

The local range was 0.64–0.81 for answerable and 0.20–0.41 for unrelated questions. It was
measured on fewer questions and has not been re-measured since hybrid search was added.

## Embedding provider (cloud)

OpenRouter serves `qwen/qwen3-embedding-8b` through three providers: Nebius (bf16),
DeepInfra, and SiliconFlow (fp8).

- Unpinned routing gave different similarity scores across runs, because requests landed
  on different providers.
- Pinned to Nebius, vectors still drifted between runs. Two embeddings of the same text
  had a cosine similarity of about 0.99995.
- Pinned to DeepInfra, sequential runs returned bit-identical vectors.
- For the same input, Nebius and DeepInfra vectors had a cosine similarity of 0.998.
- Under concurrent load, scores still varied at the third decimal with DeepInfra pinned.
- Cloud ingestion of the sample documents took 80–90 s unpinned and 20 s pinned.

This is why `.env.example` sets `EMBEDDING_PROVIDER=deepinfra`
([decision](decisions.md#pinning-the-embedding-provider)).

## Follow-up condensing

The e2e follow-up asks "Should I run FLUSHALL to fix Redis memory pressure?" and then
"Why not?". In local mode, the two condense prompts behaved very differently:

| Condense prompt (local) | Model rewrote "Why not?" |
|---|---|
| Narrowed: rewrite whenever the question has a pronoun, a leading "and" or "also", or words left out | 0/5 |
| Current: return a standalone question unchanged | 5/5 |

Searched alone in local mode, "Why not?" scored 0.514 against an unrelated runbook,
`oncall-escalation-policy.md`. That is just above the 0.5 gate. With the previous question
prepended, it scored 0.761 on `redis-memory-pressure.md`.

With the follow-up fallback in place
([decision](decisions.md#condensing-follow-ups-and-the-fallback)), the e2e follow-up check
passed 10/10 in local mode and 5/5 in cloud mode. The local runs were 5 with each prompt,
so they include the 5 runs in which the model did not rewrite the question.

## Concurrency (cloud)

Of 24 requests sent at once, all returned `200`. The wall clock was 15 s, against 124.8 s
of summed request time, and no response contained another request's search query.

In one sequential run, a provider answered `429` after two SDK retries, and the API
returned `503`.

## Latency

| | Cloud | Local (CPU) |
|---|---|---|
| Answer | median about 7 s | 30–50 s, longer for the first question after a model load |
| Fixed `no_relevant_context` answer | 3.5 and 8.8 s in two runs, for one embedding call over the network | well under a second |

## Reranker

No reranker is used. Ollama has no rerank API, and only community uploads emulate one. On
OpenRouter, Qwen3-Reranker-8B had a single provider at about 85% availability when checked
([decision](decisions.md#no-reranker)).

## Instruction-following misses

The following misses were seen in both modes, with the answer prompt unchanged:

- Q1 leaves out the 15-minute secondary page and only says "within 5 minutes".
- Q5 keeps the concrete service from the runbook, `payments-worker`, in the rollback
  commands instead of a `<service>` placeholder.
- The models use bullet or numbered lists where the prompt asks for plain prose. In cloud
  mode this happened for Q6, Q7 and t1.

## Known limitations

These follow from the results above:

- **Symptoms phrased differently from the runbook can still miss.** Q7 ranks fifth after
  fusion, so its answer is built from the wrong runbooks.
- **Full-text search is English only.** Non-English questions such as t1 rely on vector
  search alone.
- **The threshold margins are narrow.** The margin is about 0.05 on each side of 0.5 in
  cloud mode, so a new document or embedding model can move a question across the gate.
- **`answered` does not mean "covered".** When the gate opens but the passages do not
  contain the answer, as with Q15, the model says the documentation does not cover it. The
  status is still `answered`.
- **Full-text matching is broad.** Any word of the question can match, because the query
  joins the words with OR. Unrelated questions that share common words therefore get
  full-text candidates, and only the vector gate keeps them out.
- **Prompt rules are not always followed.** The misses above happen more often with the
  local 4B model.
