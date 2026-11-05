---
title: Checkout API High Latency Runbook
service: checkout-api
owner_team: team-payments
last_reviewed: 2026-10-01
---

## Summary

This runbook outlines the steps to diagnose and mitigate high latency on `checkout-api`. The `checkout-api` service is a Go application running in Kubernetes namespace `prod` and is owned by `team-payments`. It provides synchronous endpoints for merchant checkout sessions and payment creation. Because checkout is a synchronous flow, elevated latency directly degrades buyer checkout experience, causes merchant API client timeouts, and risks duplicate payment submissions due to client-side retries.

## Alert / Symptoms

This runbook is activated when Alertmanager routes a page to PagerDuty with the following condition:
- `checkout-api` p99 latency > 800 ms for 10 minutes

Observable symptoms include:
- Degradation visible on the `checkout-api` Grafana dashboard, specifically the 99th percentile response duration.
- Increased request latency reported upstream by `api-gateway` (Nginx).
- Upstream HTTP 504 Gateway Timeout or client timeout errors in Loki logs.
- PromQL alerting rule:
  ```promql
  histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{namespace="prod", job="checkout-api"}[5m])) by (le)) > 0.8
  ```

## Impact

Excessive latency in `checkout-api` affects real-time payment transactions across Quillbridge:
- **SEV1**: High latency causes cascading timeouts and failures across checkout flows for most merchants.
- **SEV2**: Latency degradation affects a subset of merchants, specific payment routes, or high-volume checkout funnels.
- **SEV3**: Intermittent latency spikes affecting internal calls with negligible merchant impact.

## Diagnosis

Follow these diagnostic steps to identify what is driving request latency:

1. **Check Resource Utilization in Kubernetes:**
   Inspect CPU and memory usage across all `checkout-api` pods in `prod`:
   ```bash
   kubectl top pods -n prod -l app=checkout-api
   ```
   Check if pods are hitting CPU limits or experiencing CPU throttling:
   ```bash
   kubectl describe pods -n prod -l app=checkout-api
   ```
   Also check if the underlying Kubernetes nodes are saturated (node disk usage > 85% or node memory pressure):
   ```bash
   kubectl top nodes
   ```

2. **Isolate Latency by Endpoint and Dependency:**
   Examine the `checkout-api` Grafana dashboard to pinpoint which layer is adding latency:
   - Go application processing time (garbage collection, CPU contention).
   - PostgreSQL database queries via PgBouncer.
   - Redis operations on `redis-cache`.
   - Message dispatch to RabbitMQ `rabbitmq-0`.

3. **Inspect Database Performance:**
   High latency in `checkout-api` is frequently tied to PostgreSQL contention on `pg-main-0`:
   - Check if PgBouncer waiting clients > 50 for 3 minutes.
   - Query PgBouncer pool saturation (pool size 100 per service):
     ```bash
     psql -h pg-main-0 -p 6432 -U pgbouncer -c "SHOW POOLS;"
     ```
   - Check `pg-main-0` for active slow queries or lock contention:
     ```sql
     SELECT pid, now() - query_start AS duration, query, state
     FROM pg_stat_activity
     WHERE state != 'idle'
     ORDER BY duration DESC
     LIMIT 10;
     ```

4. **Inspect Redis Cache Latency:**
   Check response times on `redis-cache` (Redis 7), used for session storage and idempotency keys:
   - Check memory consumption to see if it exceeds 90% of the 4 GB maxmemory limit.
   - Verify if Redis latency has spiked due to large key lookups or eviction pressure.

5. **Review Recent Deployments in Argo CD:**
   Check the Argo CD application `checkout-api` for recent syncs. A newly introduced database query without an appropriate index or an inefficient algorithmic loop can cause immediate latency regressions.

## Mitigation

Depending on the diagnosis, implement the corresponding mitigation:

1. **Scale Out Checkout API Pods:**
   If existing pods are saturated under elevated traffic volume or CPU throttling, increase the replica count:
   ```bash
   kubectl scale deployment checkout-api -n prod --replicas=12
   ```

2. **Roll Back Degraded Release:**
   If latency surged immediately following a deployment in Argo CD:
   - Navigate to the `checkout-api` application in Argo CD.
   - Initiate a rollback to the previous stable release.
   - Confirm via Grafana that p99 latency decreases below 800 ms.

3. **Restart Pods Under Unrecoverable Latency:**
   If pods have exhausted internal connection pools or are stuck in unhealthy states:
   ```bash
   kubectl rollout restart deployment checkout-api -n prod
   ```

4. **Address Database Bottlenecks:**
   - If PgBouncer waiting clients exceed 50, terminate blocking or rogue long-running queries on `pg-main-0`:
     ```sql
     SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid = <pid>;
     ```
   - Ensure query load is balanced and consult `team-ledger` if ledger-related locks are blocking checkout tables.

5. **Verify Recovery:**
   Confirm on the Grafana dashboard that `checkout-api` p99 latency remains below 800 ms for at least 10 minutes.

## Escalation

- **Primary On-Call**: Must acknowledge the PagerDuty alert within 5 minutes.
- **Secondary On-Call**: Paged at 15 minutes if the primary on-call fails to acknowledge.
- **SEV1 Escalation**: If latency degradation causes checkout to fail for most merchants:
  - Page the engineering manager on-call immediately.
  - Appoint an Incident Commander.
  - Establish communications in Slack channel `#inc-major`.
- **Team Coordination**:
  - Keep `#team-payments` updated on mitigation progress.
  - Coordinate with `#oncall-platform` for cluster, networking, or PgBouncer/Redis infrastructure issues.
  - Engage `#team-ledger` if PostgreSQL transaction locks involve ledger writes.
- **Post-Incident**: A postmortem is required within 5 business days for all SEV1 and SEV2 incidents.

## Related runbooks

- `checkout-api-high-error-rate.md`
- `deploy-rollback-procedure.md`
- `oncall-escalation-policy.md`
- `severity-levels.md`
- `postmortem-process.md`
- `postgres-connection-pool-exhausted.md`
- `redis-memory-pressure.md`
- `node-disk-pressure.txt`
