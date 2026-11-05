---
title: Redis Memory Pressure
service: Redis
owner_team: team-platform
last_reviewed: 2026-10-01
---

## Summary

Quillbridge uses Redis 7, instance `redis-cache`, for merchant sessions and idempotency keys. The instance is configured with `maxmemory 4 GB`. When memory usage goes above 90% of `maxmemory`, the page `Redis memory usage > 90% of maxmemory` fires.

This alert matters more than its name suggests, because the two things Redis holds are both load-bearing for payments. An evicted idempotency key means a retried checkout request is no longer recognised as a retry, and the merchant can be charged twice for the same attempt. An evicted session logs the merchant out mid-checkout.

## Alert / Symptoms

The page that brings you here is `Redis memory usage > 90% of maxmemory`, which is roughly 3.6 GB of the 4 GB budget.

Symptoms to look for around the same time:

- Merchants reporting being logged out mid-checkout or having to sign in again.
- Duplicate or retried charges appearing for the same checkout attempt.
- `checkout-api` 5xx rate above 2% for 5 minutes, if clients are seeing write errors against the cache.
- `checkout-api` p99 latency above 800 ms for 10 minutes, if requests are waiting on slow or failing cache calls.

Internal symptom: the `evicted_keys` counter in Redis stats climbing steadily, which means keys are already being dropped before you act.

## Impact

Treat this as **SEV2** (degraded checkout or payments for a subset of merchants) as soon as evictions are visible, because eviction of idempotency keys creates a duplicate-charge risk. Escalate to **SEV1** if checkout is down or payments are failing for most merchants, and to **SEV1** if confirmed duplicate charges are affecting merchants, since that is direct financial impact. If only internal tooling reads the cache and no merchant request is affected, keep it at **SEV3**.

State the duplicate-charge risk explicitly in the incident channel. It changes what the incident commander prioritises.

## Diagnosis

Start with the instance's own view of memory:

```bash
kubectl -n prod exec -it redis-cache -- redis-cli INFO memory
kubectl -n prod exec -it redis-cache -- redis-cli INFO stats
kubectl -n prod exec -it redis-cache -- redis-cli INFO keyspace
kubectl -n prod exec -it redis-cache -- redis-cli CONFIG GET maxmemory maxmemory-policy
```

In `INFO keyspace`, compare `keys` with `expires`. A growing `keys` count with few expiring keys means something is writing keys without a TTL, which is the most common cause of a steadily filling cache.

Confirm the ratio in Prometheus:

```promql
redis_memory_used_bytes{instance="redis-cache"} / redis_memory_max_bytes{instance="redis-cache"}
```

Find the largest keys:

```bash
kubectl -n prod exec -it redis-cache -- redis-cli --bigkeys
```

Scan a suspect prefix and check TTLs on the sampled keys. Replace the pattern and key with the prefix that actually exists:

```bash
kubectl -n prod exec -it redis-cache -- redis-cli --scan --pattern 'session:*' --count 100
kubectl -n prod exec -it redis-cache -- redis-cli TTL "session:<sample-key>"
```

A key that returns `-1` never expires. That single key, or a large family of them, can account for most of the memory.

Identify the writer by correlating the growth rate with the Grafana dashboard for each service (dashboards are named the same as the service) and by reading that service's Redis errors in Loki.

## Mitigation

Do not run `FLUSHALL` as a reflex. It deletes every session and every idempotency key, which logs out all merchants and re-enables duplicate charge attempts across the whole platform.

Reclaim space in this order:

1. Delete the specific oversized or expired keys you identified. Start with keys with no TTL or a stale TTL that no longer matters:

```bash
kubectl -n prod exec -it redis-cache -- redis-cli DEL "session:<sample-key>"
```

2. If the configured eviction policy drops keys, the `evicted_keys` counter in `INFO stats` will confirm it. If the policy instead refuses writes, clients will show `OOM command not allowed when used memory > 'maxmemory'` in Loki. The two cases need different fixes: eviction means you must free keys quickly, refused writes means you must free keys before traffic fails.

3. Find the service that writes keys without a TTL and fix it there. The Argo CD application has the same name as the service, so a fix to `checkout-api` rolls out through that application.

4. If the growth started right after a deploy, roll back that deploy and let memory drain; see deploy-rollback-procedure.md.

5. Increasing the memory limit for the Redis workload is a reviewed change owned by the team that operates the instance. It is not an on-call action, and it does not fix an unbounded key growth problem.

## Escalation

Acknowledge the PagerDuty page within 5 minutes. If it is not acknowledged, the secondary on-call is paged at 15 minutes.

A SEV1 pages the engineering manager on-call immediately and assigns an incident commander. For SEV1 and SEV2, run the incident in the `#inc-major` channel and keep `#oncall-platform` informed. Pull in `#team-payments` because sessions and idempotency keys sit on the checkout path.

Every SEV1 and SEV2 requires a postmortem within 5 business days.

## Related runbooks

- checkout-api-high-error-rate.md
- checkout-api-high-latency.md
- payments-worker-queue-backlog.md
- deploy-rollback-procedure.md
- severity-levels.md
- oncall-escalation-policy.md
- postmortem-process.md
