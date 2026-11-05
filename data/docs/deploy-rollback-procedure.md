---
title: Deploy Rollback Procedure
service: all
owner_team: team-platform
last_reviewed: 2026-10-01
---

## Scope

All five production services — `api-gateway`, `checkout-api`, `payments-worker`, `ledger-service`, `notification-service` — deploy through Argo CD, with one Argo CD application per service named after the service. This procedure covers rolling a service back to a previous revision when a deploy causes or worsens an incident: Argo CD first, `kubectl rollout undo` as the fallback.

## Before you roll back

1. Confirm a deploy is the likely cause. Compare the deploy time in the Argo CD application history with when symptoms started on the Grafana dashboard named after the service. If the service degraded right after a sync, a rollback is usually the fastest mitigation.
2. Check the current state and the deploy history:

   ```bash
   argocd app get checkout-api
   argocd app history checkout-api
   ```

   `history` lists recently deployed revisions with their IDs. Identify the last known-good revision ID before proceeding.
3. If an incident is in progress, post your intent in `#inc-major` (SEV1/SEV2) or the relevant team channel before rolling back, so nobody is shipping a fix on top of a moving target.

## Roll back with Argo CD (preferred)

Pause automated sync first, so Argo CD does not re-apply the bad revision while you work:

```bash
argocd app set checkout-api --sync-policy none
```

Then roll back to the last known-good revision ID from the history:

```bash
argocd app rollback checkout-api 42
```

Watch the rollout finish and the pods come ready:

```bash
kubectl -n prod rollout status deployment/checkout-api
kubectl -n prod get deploy checkout-api
kubectl -n prod get pods | grep checkout-api
```

## Fallback: kubectl rollout undo

If the Argo CD CLI or UI is unavailable, roll the Kubernetes deployment back directly:

```bash
kubectl -n prod rollout history deployment/payments-worker
kubectl -n prod rollout undo deployment/payments-worker
# or to a specific revision:
kubectl -n prod rollout undo deployment/payments-worker --to-revision=3
kubectl -n prod rollout status deployment/payments-worker
```

This changes only the live deployment, and it is a stopgap: the next Argo CD sync can re-apply the bad revision. As soon as Argo CD is reachable again, either repeat the rollback there or revert the commit/manifest that caused the problem before re-enabling sync.

## After the rollback: verify recovery

Confirm on the service's Grafana dashboard (same name as the service) and in Loki that symptoms are gone — not just that new pods are Running:

- `checkout-api`: 5xx rate back under 2% and p99 latency back under 800 ms.
- `payments-worker`: `payments.charge` depth below 10,000 messages and draining steadily.
- `ledger-service`, `notification-service`, `api-gateway`: error and latency graphs back at their normal baseline.

Then close out the sync policy:

- If the bad revision has been reverted or superseded by a good deploy, re-enable automated sync:

  ```bash
  argocd app set checkout-api --sync-policy automated
  ```

- Leave a note in the incident channel with the revision you rolled back to and why, so the postmortem has the facts.

A rollback is a mitigation, not a fix: the defective change still needs a proper fix, and SEV1/SEV2 incidents still require a postmortem within 5 business days.

## Related runbooks

- `checkout-api-high-error-rate.md`, `checkout-api-high-latency.md` — rollback is the standard mitigation when either page starts right after a checkout-api deploy
- `payments-worker-queue-backlog.md` — includes the rollback decision for a bad worker release
- `notification-service-delivery-failures.md` — same pattern for notification-service deploys
- `severity-levels.md` — classifying the incident while you roll back
