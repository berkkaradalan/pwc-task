---
title: Ledger Service Balance Mismatch
service: ledger-service
owner_team: team-ledger
last_reviewed: 2026-10-01
---

## Summary

`ledger-service` writes double-entry records for every payment to PostgreSQL on `pg-main-0`, through PgBouncer. For each transaction, debits must equal credits. A balance mismatch means that invariant broke: entries are duplicated (retries and replays are the classic sources) or missing (ledger writes failed while the payment proceeded anyway).

A third possibility is the most common one: nothing is wrong with the data at all. Reconciliation that read the replica `pg-main-1` while it lagged more than 30 seconds behind the primary can report a false mismatch, because recent entries had not replayed yet. Establish which of the three situations you are in before touching anything.

## Alert / Symptoms

There is no dedicated mismatch page. The signal usually arrives as:

- The reconciliation job failing or reporting discrepancies, visible on the `ledger-service` Grafana dashboard.
- Merchants, or merchant support, reporting balances or payment histories that look wrong.
- Duplicate charges showing for the same checkout attempt.
- The follow-on to another incident: replays after a `payments.charge` backlog drain, retry storms during a card processor outage, ledger write failures during PgBouncer pool exhaustion, or idempotency keys evicted from `redis-cache` under memory pressure.

## Impact

**SEV1** if duplicated or missing records affect payments for most merchants — for example a replay duplicated every charge in a large backlog. **SEV2** if the mismatch is limited to a subset of merchants or a bounded time window. **SEV3** if reconciliation tooling failed but the underlying data verifies clean. This is financial data: a confirmed duplicate charge affecting merchants is direct financial impact and is treated as SEV1-grade even when checkout itself is healthy.

## Diagnosis

1. Re-run reconciliation against the primary. If the original run read `pg-main-1`, its results are suspect while replica lag exceeds the 30-second alert threshold. Re-run against `pg-main-0`:

```bash
kubectl -n prod exec -it pg-main-0 -- psql -U postgres \
  -c "select transaction_id, sum(debit) as debits, sum(credit) as credits from ledger_entries group by transaction_id having sum(debit) <> sum(credit) limit 20;"
```

Table and column names here are illustrative — `team-ledger` owns the canonical reconciliation query, so use theirs. A clean primary result with a dirty replica result means replica lag, not corruption: follow postgres-replica-lag.md and re-run once the replica has caught up.

2. Measure the lag if it is not already visible on the dashboard:

```bash
kubectl -n prod exec -it pg-main-1 -- psql -U postgres \
  -c "select now() - pg_last_xact_replay_timestamp() as since_replay;"
```

3. Classify real discrepancies:
   - **Duplicated entries** — the same charge posted twice. Check whether the window overlaps client-side retries after checkout timeouts, a `payments.charge` replay after backlog recovery, or a card processor retry storm.
   - **Missing entries** — charges that succeeded with no ledger record. Check `ledger-service` logs in Loki for write failures in the window, and whether its PgBouncer pool (100 connections) was exhausted at the time; a known failure mode is payments created by `checkout-api` with no matching ledger record.

4. Bound the window. Identify the earliest and latest affected transaction IDs and the count, then cross-check Loki and the Argo CD history for the `ledger-service` and `payments-worker` applications for changes inside the window.

## Mitigation

Never fix a mismatch by editing rows. Do not run `UPDATE` or `DELETE` against ledger tables to force the sums to balance — not even for one row, not even mid-SEV1. Ledger corrections are new compensating double-entry entries that reference the original transaction, written by `team-ledger` through their reviewed process. On-call quantifies and freezes; on-call never edits financial records.

1. Stop the duplicate source first. If retries or replays are still producing duplicates, stopping the replay precedes any correction, or every correction has to be done twice.
2. Freeze and document. Record the affected window, transaction count, and classification in `#team-ledger`, and in `#inc-major` for SEV1/SEV2, so the correction set is agreed before entries are written.
3. Corrections. `team-ledger` writes the compensating entries in batches; re-run the canonical reconciliation after each batch.
4. Verify. Reconciliation clean against both `pg-main-0` and a caught-up `pg-main-1`, with no new discrepancies appearing on the `ledger-service` Grafana dashboard.

## Escalation

Acknowledge any related page within 5 minutes; if unacknowledged, the secondary on-call is paged at 15 minutes.

Involve `#team-ledger` from the first minute — this is their data model. If duplicated or missing records affect payments for most merchants, it is a SEV1: the engineering manager on-call is paged immediately, an incident commander is assigned, and the incident runs in `#inc-major`. A bounded, subset-only mismatch is SEV2 and also runs in `#inc-major`. Loop in `#team-payments` when duplicates trace back to checkout retries or `payments.charge` replays, and `#oncall-platform` when PostgreSQL or PgBouncer contributed. If merchants must be informed, draft the wording from incident-communication-templates.md and send it via `notification-service`.

Every SEV1 and SEV2 requires a postmortem within 5 business days, listing the affected window and the compensating entries written.

## Related runbooks

- postgres-replica-lag.md
- postgres-connection-pool-exhausted.md
- redis-memory-pressure.md
- payments-worker-queue-backlog.md
- card-processor-outage.md
- incident-communication-templates.md
- severity-levels.md
- postmortem-process.md
