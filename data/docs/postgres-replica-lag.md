---
title: PostgreSQL Replica Lag
service: PostgreSQL
owner_team: team-platform
last_reviewed: 2026-10-01
---

## Summary

Quillbridge runs PostgreSQL 16 with one primary, `pg-main-0`, and one read replica, `pg-main-1`. The replica streams WAL from the primary and serves read traffic. When the replica falls more than 30 seconds behind, the page `PostgreSQL replica lag > 30 seconds` fires.

Replica lag is a correctness problem dressed as a performance problem. A read served by a lagging replica can show state that the primary already changed: a merchant reloading the checkout page right after a payment may see the payment as still pending, and `ledger-service` reads that assume read-after-write can return stale double-entry records. The primary remains authoritative throughout.

## Alert / Symptoms

The page that brings you here is `PostgreSQL replica lag > 30 seconds`.

Symptoms to expect around the same time:

- Merchants reloading a checkout page and seeing an older state than the one they just completed.
- `checkout-api` p99 latency above 800 ms for 10 minutes, if reads are being retried against a saturated or slow replica.
- `checkout-api` 5xx rate above 2% for 5 minutes, when the application surfaces a read timeout against the replica.

Internal symptoms: reporting or reconciliation jobs that read from the replica return values that do not match the primary, and latency on the replica path climbing while the primary looks fine.

## Impact

This is usually **SEV3** if only internal consumers read from the replica and no merchant request can fail. It becomes **SEV2** if a subset of merchants see stale checkout or ledger state, and **SEV1** if the condition is blocking payment recording or checkout for most merchants.

Severity depends on who reads from the replica. Confirm that before declaring the incident minor. If `ledger-service` reconciliation depends on replica reads and reconciliation is now producing false mismatches, treat it as merchant-affecting even though no checkout failed.

## Diagnosis

Compare the write and replay positions on the primary:

```bash
kubectl -n prod exec -it pg-main-0 -- psql -U postgres \
  -c "select client_addr, application_name, state, sent_lsn, write_lsn, flush_lsn, replay_lsn, sync_state, write_lag, flush_lag, replay_lag from pg_stat_replication;"
```

Then check the receiving side and how long replay has been stalled:

```bash
kubectl -n prod exec -it pg-main-1 -- psql -U postgres \
  -c "select status, sender_host, received_lsn, latest_end_lsn, last_msg_send_time from pg_stat_wal_receiver;"
kubectl -n prod exec -it pg-main-1 -- psql -U postgres \
  -c "select pg_last_wal_receive_lsn() as receive_lsn, pg_last_wal_replay_lsn() as replay_lsn, now() - pg_last_xact_replay_timestamp() as since_replay;"
```

If `state` is not `streaming`, or `pg_stat_wal_receiver` has no row, the replica lost its connection and is not catching up at all.

Next, look for work on the replica that blocks replay:

```bash
kubectl -n prod exec -it pg-main-1 -- psql -U postgres \
  -c "select pid, application_name, state, wait_event_type, wait_event, now()-query_start as runtime, left(query,140) as query from pg_stat_activity where datname is not null order by query_start;"
```

A long-running read transaction on the replica can hold back WAL cleanup and stall replay. Two clients that are merely slow are normal; one that has been running for many minutes is the usual culprit.

Then confirm the pod itself is healthy and check its logs:

```bash
kubectl -n prod get pods -o wide | grep pg-main
kubectl -n prod describe pod pg-main-1
kubectl -n prod logs pg-main-1 --tail=200 | grep -Ei 'replication|wal|fatal|error'
```

Pull the same stream from Loki if the pod was restarted and the previous container's logs are gone.

## Mitigation

If a long-running read on the replica is blocking replay, end it and let catch-up proceed:

```bash
kubectl -n prod exec -it pg-main-1 -- psql -U postgres \
  -c "select pg_terminate_backend(pid) from pg_stat_activity where now()-query_start > interval '10 minutes' and state = 'active';"
```

Watch the replay position advance afterwards with the `pg_last_wal_replay_lsn()` query above.

If the receiver is disconnected and is not reconnecting, restart the replica pod. `pg-main-0` holds the authoritative copy, and the replica resyncs from the primary:

```bash
kubectl -n prod delete pod pg-main-1
```

Expect lag to take time to unwind if the WAL backlog is large. Keep it visible in the incident channel until the lag alert clears.

If the lag started right after a deploy, suspect that change and consider rolling it back; the write volume or query shape may have changed. See deploy-rollback-procedure.md.

Do not remove replication slots or force WAL switching by hand while the replica is behind. That can make recovery worse and is not a recovery action.

## Escalation

Acknowledge the PagerDuty page within 5 minutes. If it is not acknowledged, the secondary on-call is paged at 15 minutes.

A SEV1 pages the engineering manager on-call immediately and assigns an incident commander. For SEV1 and SEV2, run the incident in the `#inc-major` channel, keep `#oncall-platform` posted, and involve `#team-ledger` if `ledger-service` reads or reconciliation are affected, or `#team-payments` if checkout is affected.

Every SEV1 and SEV2 requires a postmortem within 5 business days.

## Related runbooks

- postgres-connection-pool-exhausted.md
- checkout-api-high-latency.md
- deploy-rollback-procedure.md
- severity-levels.md
- oncall-escalation-policy.md
- postmortem-process.md
