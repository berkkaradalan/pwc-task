---
title: PostgreSQL Connection Pool Exhaustion (PgBouncer)
service: PostgreSQL
owner_team: team-platform
last_reviewed: 2026-10-01
---

## Summary

Every Quillbridge service that reaches PostgreSQL connects through PgBouncer in the `prod` namespace, and PgBouncer is configured with a pool size of 100 connections per service. When clients queue up waiting for a server connection, the page `PgBouncer waiting clients > 50 for 3 minutes` fires.

The database is usually healthy. Something upstream is holding connections it never gives back, so one service's pool drains and every new request for that service waits. This runbook covers the first thirty minutes: find the client that stopped returning connections, free the pool, and decide whether merchants are affected.

## Alert / Symptoms

The page that wakes you is `PgBouncer waiting clients > 50 for 3 minutes`. It often arrives together with other alerts, and the combination narrows the suspect quickly:

- `checkout-api` p99 latency above 800 ms for 10 minutes.
- `checkout-api` 5xx rate above 2% for 5 minutes.

If either checkout alert fires alongside the pool page, `checkout-api` is the most likely holder. If neither fires, suspect a service whose traffic is mostly internal, such as `ledger-service` or `notification-service`.

Merchant-facing symptoms are checkouts that spin and eventually fail, payments that are created but feel "stuck", and requests that hang until a timeout. In Loki, look for the affected service's logs containing `sorry, too many clients already` or `FATAL: remaining connection slots are reserved for superusers`.

## Impact

Connection exhaustion is usually **SEV2** (degraded checkout or payments for a subset of merchants) once checkout requests time out. It becomes **SEV1** if checkout is down or payments fail for most merchants. If only internal tooling is affected, keep it at **SEV3**.

One complication to check early: `ledger-service` writes double-entry records to PostgreSQL. If its pool is exhausted, `checkout-api` can create payments with no matching ledger record, turning a performance problem into a data-completeness problem.

## Diagnosis

Check which service's pool is draining. These PromQL queries run against the PgBouncer exporter:

```promql
sum by (service) (pgbouncer_pools_client_waiting_connections)
sum by (service) (pgbouncer_pools_client_active_connections)
  / sum by (service) (pgbouncer_pools_client_max_connections)
```

The second query is the saturation ratio; at 100 maximum connections per service, a ratio near 1.0 with a non-zero waiting count is the pool-exhaustion signature.

Confirm the PgBouncer and database pods are running, then look at the server side of the primary:

```bash
kubectl -n prod get pods | grep -Ei 'pgbouncer|pg-main'
kubectl -n prod exec -it pg-main-0 -- psql -U postgres \
  -c "select state, count(*) from pg_stat_activity group by state order by 2 desc;"
kubectl -n prod exec -it pg-main-0 -- psql -U postgres \
  -c "select pid, application_name, state, wait_event_type, now()-xact_start as xact_age, left(query,120) as query from pg_stat_activity where datname is not null order by xact_start;"
```

Look specifically for `idle in transaction` sessions with a long `xact_age`, long-running `active` queries, and blocked sessions waiting on a lock. Those are the sessions that never release their slot.

Check whether PostgreSQL itself is at its own connection ceiling, which is a different failure with a similar page:

```bash
kubectl -n prod exec -it pg-main-0 -- psql -U postgres -c "show max_connections;"
kubectl -n prod exec -it pg-main-0 -- psql -U postgres -c "select count(*) from pg_stat_activity;"
```

Finally, correlate with the Grafana dashboard whose name matches the suspect service, and pull the service's logs from Loki to find the request pattern that stopped.

## Mitigation

If a single service is the holder, bounce it. Killing the pod drops its open connections immediately and the pool refills from a clean client:

```bash
kubectl -n prod rollout restart deployment/checkout-api
kubectl -n prod rollout status deployment/checkout-api
```

Use the deployment named after the affected service (`checkout-api`, `ledger-service`, `notification-service`, `api-gateway`). These are Argo CD applications with the same name as the service, so a rollout restart is the standard action and requires no image change. Re-check the waiting-clients query after the rollout; if the count climbs again within minutes, the service is leaking connections on every request and the fix is a code change, not a restart.

If the holder is a stuck transaction rather than a service, free the server slots directly:

```bash
kubectl -n prod exec -it pg-main-0 -- psql -U postgres \
  -c "select pg_terminate_backend(pid) from pg_stat_activity where state = 'idle in transaction' and xact_start < now() - interval '5 minutes';"
```

Terminating backends aborts the client transaction, so only do it for sessions you confirmed are stuck.

Reduce pressure while you work: if read traffic can be shifted, point read-only queries at the read replica `pg-main-1` so the primary keeps its slots for writes.

Raising the PgBouncer pool size above 100 per service is a configuration change with a reviewed path, not an on-call action. More pool slots mean more PostgreSQL backends, which can push the primary into its own `max_connections` ceiling.

## Escalation

Acknowledge the PagerDuty page within 5 minutes. If it is not acknowledged, the secondary on-call is paged at 15 minutes.

For a SEV1, the engineering manager on-call is paged immediately and an incident commander is assigned. Run the incident in the `#inc-major` channel for SEV1 and SEV2, keep `#oncall-platform` informed, and pull in `#team-payments` when `checkout-api` is involved or `#team-ledger` when `ledger-service` is involved.

Every SEV1 and SEV2 requires a postmortem within 5 business days.

## Related runbooks

- postgres-replica-lag.md
- checkout-api-high-error-rate.md
- checkout-api-high-latency.md
- deploy-rollback-procedure.md
- severity-levels.md
- oncall-escalation-policy.md
- postmortem-process.md
