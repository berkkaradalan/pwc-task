---
title: Card Processor Outage
service: payments-worker
owner_team: team-payments
last_reviewed: 2026-10-01
---

## Summary

`payments-worker` calls an external card processor to execute each charge. When the processor has an outage, Quillbridge cannot fix it — there is no rollback, no restart, no config change that helps. The only correct moves are to confirm the outage is on the provider's side, stop hammering it with retries, and keep merchants informed.

## Alert / Symptoms

- Charges fail at unusually high rates; payments-worker errors spike on its Grafana dashboard and in Loki, typically with timeouts or uniform declines from the processor endpoint.
- `payments.charge` queue depth climbs — the > 10,000 messages alert may fire as failed charges retry or consumption stalls.
- Merchants report declined or stuck payments.
- The card processor's public status page shows an active incident.

## Impact

Charges failing for most merchants is SEV1; a subset failing is SEV2. Because the failure is external, the response focuses on what Quillbridge tells merchants and how much retry pressure we generate — not on restoring a service we do not operate.

## Diagnosis

1. Check the card processor's public status page first. If it declares an incident matching our error window, the diagnosis is done — everything else is confirmation.
2. Confirm the failure signature points at the provider, not at us: errors in Loki should be timeouts or provider-side declines at the processor endpoint, not connection errors to RabbitMQ or PostgreSQL on our side.
3. Confirm our side is healthy: payments-worker pods running and consuming, RabbitMQ reachable (`rabbitmqctl status` on rabbitmq-0), no recent payments-worker deploy (`argocd app get payments-worker`).
4. If our side is healthy but the provider's status page is still clean, keep checking both — partial provider degradations are often posted late. Compare error timing against the provider's incident history on the status page.

## Mitigation

1. Declare the incident and say plainly in `#inc-major` that the root cause is external. That changes the goal: mitigate and communicate, not repair.
2. Pause retries so we stop hammering a struggling provider and stop burning effort on doomed attempts. Coordinate the exact mechanism with team-payments — the queue retains messages, so pausing does not lose charges.
3. Inform merchants through the normal merchant channels: notification-service sends emails and webhooks to merchants. Use the approved wording from incident-communication-templates.md — say what merchants should expect (delayed or failed charges) without promising a fix time we do not control.
4. Keep processing what can be processed. If some charges still succeed, let them.
5. When the provider declares recovery, resume and expect a catch-up wave: the queue will be deep and the processor may be slow at first. Work the drain per payments-worker-queue-backlog.md, and watch for duplicate-charge symptoms (ledger-service-balance-mismatch.md) as retries and replays overlap.

## Escalation

SEV1: engineering manager on-call paged immediately, incident commander assigned, incident run in `#inc-major`. Loop in `#team-payments` for the retry pause and consumer decisions. Because the cause is external, include the provider's status-page state in every update. The postmortem, due within 5 business days, must focus on our detection and mitigation quality — not on the provider's uptime.

## Related runbooks

- payments-worker-queue-backlog.md — the post-outage catch-up backlog
- rabbitmq-node-down.md — when charges stall but the processor is healthy
- ledger-service-balance-mismatch.md — duplicates from retries and replays
- notification-service-delivery-failures.md — if merchant emails or webhooks also struggle
- incident-communication-templates.md — approved wording for merchant updates
- severity-levels.md
