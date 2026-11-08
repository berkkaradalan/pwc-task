# Evaluation

The sample corpus in `data/docs` has 16 runbooks, which make 103 chunks. The retrieval
results and the calibration below were measured on this corpus. Sections marked
"12-runbook corpus" were measured earlier, before four runbooks were added, when the corpus
had 12 runbooks and 79 chunks; they are kept as history.

Requests were sent one at a time unless stated otherwise. Each result names the mode it
comes from:

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
| Q15 | answerable | What should I do when rabbitmq-0 goes down? | `rabbitmq-node-down.md` |
| Q16 | missing runbook | What should I do when Loki stops ingesting logs? | none; the runbooks use Loki to read logs, but none covers a Loki failure |
| n1 | answerable | Is there a second RabbitMQ broker to fail over to? | `rabbitmq-node-down.md` |
| n2 | answerable | Can I fix a ledger balance mismatch by editing the rows directly? | `ledger-service-balance-mismatch.md` |
| n3 | answerable | What should the SEV1 initial update in #inc-major contain? | `incident-communication-templates.md` |
| n4 | answerable | A node is above 85% disk usage but every pod is still running. How severe is that? | `node-disk-pressure.txt` |
| s1 | symptom | Merchants say they were charged twice for the same checkout attempt. What could cause this? | `redis-memory-pressure.md` |
| s2 | symptom | Reconciliation jobs report ledger mismatches but no checkout failed. How serious is this? | `ledger-service-balance-mismatch.md` or `postgres-replica-lag.md` |
| t1 | non-English | Q7 asked in Turkish: "The customer refreshes the page right after paying and still sees the payment as pending. What could be the cause?" | `postgres-replica-lag.md` |
| o1 | unrelated | How do I renew the TLS certificate on api-gateway? | none |
| o2 | unrelated | How do I bake sourdough bread? | none |

A question counts as a hit when the expected runbook is among the 4 sources returned
(`RETRIEVAL_TOP_K`). Q15 was the missing-runbook question until `rabbitmq-node-down.md` was
added; Q16 replaced it. For s2, `postgres-replica-lag.md` was the expected runbook on the
12-runbook corpus. `ledger-service-balance-mismatch.md` now covers the symptom directly,
including reconciliation that read a lagging replica, so either counts as a hit.

## Retrieval (cloud, 16-runbook corpus)

Of the 20 questions with an expected runbook, 18 were hits. Thirteen of them ranked their
expected runbook first after fusion. These are the ranks of the other seven:

| Question | Vector-only rank | Full-text rank | Fused rank |
|---|---|---|---|
| Q6 | 6 | 3 | **3** |
| Q7 | 9 | 1 | 5 |
| Q8 | 1 | 2 | **2** |
| n3 | 1 | 6 | **2** |
| s1 | 7 | 1 | **2** |
| s2 (`ledger-service-balance-mismatch.md`) | 1 | 1 | **1** |
| s2 (`postgres-replica-lag.md`) | 12 | 2 | 5 |
| t1 (Turkish) | 9 | no match | 9 |

The two misses are the same as before the corpus grew. Q7 still ranks fifth, and its answer
blamed the `payments.charge` queue backlog. t1 matches no chunk by full text and ranks
ninth. s2 is now answered from the ledger runbook, which classifies the case as SEV2.

Q13, Q14, o1 and o2 returned `no_relevant_context`. o1 and Q14 each had 20 full-text
matches, which is why full text must not open the relevance gate.

## Similarity threshold calibration (cloud, 16-runbook corpus)

The top vector similarity per question:

| Group | Range |
|---|---|
| Answerable (Q1–Q12, Q15, n1–n4, s1, s2, t1) | 0.61 (Q7) – 0.91 (Q11) |
| Unrelated (Q13, Q14, o1, o2) | 0.21 (o2) – 0.44 (o1) |
| Missing runbook (Q16) | 0.58 |

The default `RETRIEVAL_MIN_SIMILARITY` of 0.5 lies in the gap. The margin is about 0.06
below it (o1) and about 0.11 above it (Q7). Q16 passes the gate with 0.58, and the model
answers "The documentation does not cover what to do when Loki stops ingesting logs."

On the 12-runbook corpus the answerable range started lower, at 0.56 (s2), so the margin
above the threshold was about 0.05. The local range was 0.64–0.81 for answerable and
0.20–0.41 for unrelated questions. It was measured on fewer questions, on the 12-runbook
corpus, before hybrid search was added, and has not been re-measured since.

## Hybrid retrieval history (cloud, 12-runbook corpus)

Before full-text search was added, vector search alone ranked the expected runbook for the
two symptom questions as follows:

| Question | Cloud | Local |
|---|---|---|
| Q6 (`redis-memory-pressure.md`) | 6 | 10 |
| Q7 (`postgres-replica-lag.md`) | 8–10 | 8 |

Neither reached the top 4 in either mode, which led to adding full-text search
([decision](decisions.md#hybrid-retrieval-with-rrf-and-a-vector-only-gate)).

Across the 20 questions of that set, hits rose from 14/20 before to 17/20 after. There were
no regressions: every question that was a hit before stayed a hit.

| Question | Vector-only rank | Fused rank | Full-text rank |
|---|---|---|---|
| Q6 | 6 | **3** | 3 |
| Q7 | 8 | 5 | 1 |
| s1 | 7 | **3** | 1 |
| s2 (`postgres-replica-lag.md`) | 7 | **1** | 1 |
| t1 (Turkish) | 8 | 8 | no match |

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
- On the 12-runbook corpus, cloud ingestion took 80–90 s unpinned and 20 s pinned. A
  pinned `--reindex` of the 16-runbook corpus took 75 s in one run.

This is why `.env.example` sets `EMBEDDING_PROVIDER=deepinfra`
([decision](decisions.md#pinning-the-embedding-provider)).

## Follow-up condensing (12-runbook corpus)

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

## Concurrency (cloud, 12-runbook corpus)

Of 24 requests sent at once, all returned `200`. The wall clock was 15 s, against 124.8 s
of summed request time, and no response contained another request's search query.

In one sequential run, a provider answered `429` after two SDK retries, and the API
returned `503`. Provider `429` responses were also seen during cloud ingestion.

## Latency (12-runbook corpus)

| | Cloud | Local (CPU) |
|---|---|---|
| Answer | median about 7 s | 30–50 s, longer for the first question after a model load |
| Fixed `no_relevant_context` answer | 3.5 and 8.8 s in two runs, for one embedding call over the network | well under a second |

In the cloud run on the 16-runbook corpus, the 21 answered questions took 2.6–12.3 s each,
and the 4 `no_relevant_context` answers 1.3–4.4 s.

## Reranker

No reranker is used. Ollama has no rerank API, and only community uploads emulate one. On
OpenRouter, Qwen3-Reranker-8B had a single provider at about 85% availability when checked
([decision](decisions.md#no-reranker)).

## Instruction-following misses (12-runbook corpus)

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
- **The threshold margins are small.** About 0.06 below 0.5 and 0.11 above it in cloud
  mode, so a new document or embedding model can move a question across the gate.
- **`answered` does not mean "covered".** When the gate opens but the passages do not
  contain the answer, as with Q16, the model says the documentation does not cover it. The
  status is still `answered`.
- **Full-text matching is broad.** Any word of the question can match, because the query
  joins the words with OR. Unrelated questions that share common words therefore get
  full-text candidates, and only the vector gate keeps them out.
- **Prompt rules are not always followed.** The misses above happen more often with the
  local 4B model.
