---
title: Incident Severity Levels
service: all
owner_team: team-platform
last_reviewed: 2026-10-01
---

## Overview

| Level | One-line definition |
| --- | --- |
| SEV1 | Checkout down or payments failing for most merchants |
| SEV2 | Degraded checkout or payments for a subset of merchants |
| SEV3 | Internal impact only, no merchant impact |
| SEV4 | Cosmetic or low-risk issue |

## Definitions and examples

**SEV1 — checkout down or payments failing for most merchants.** The core product is unavailable or failing at a scale that affects most of the merchant base. Examples:

- `checkout-api` is returning errors broadly: the 5xx alert (> 2% for 5 minutes) has fired and the error rate keeps climbing, or the service is not serving traffic at all.
- `payments-worker` has stopped consuming the `payments.charge` queue and charges fail or stall for most merchants.
- The PostgreSQL primary `pg-main-0` is unavailable, so no payment can be recorded.

**SEV2 — degraded checkout or payments for a subset of merchants.** The product works, but a noticeable group of merchants has a worse experience. Examples:

- `checkout-api` p99 latency above 800 ms for 10 minutes: checkout completes but is slow.
- `payments.charge` queue depth above 10,000 messages: charges are delayed, not lost.
- PostgreSQL replica lag above 30 seconds: merchants served from the replica `pg-main-1` see stale data.
- Redis memory above 90% of the 4 GB maxmemory, with sessions or idempotency keys being evicted for some merchants.

**SEV3 — internal impact only, no merchant impact.** Engineers or internal tooling are affected; merchants are not. Examples:

- Node disk usage above 85% with capacity at risk but no service degradation yet.
- A broken Grafana dashboard or an alert that fired wrongly.
- A failed deploy where the previous version still serves correctly.

**SEV4 — cosmetic or low-risk.** A typo in a `notification-service` email template, a mislabeled Grafana panel, minor layout issues in merchant emails. Nothing is functionally broken.

## How to pick a severity

Classify by merchant impact, not by which alert fired. Ask three questions: who is affected (most merchants, a subset, or nobody external), how are they affected (cannot pay, degraded, cosmetic), and is the impact growing or shrinking.

- When torn between two levels, pick the higher one and downgrade later if the picture improves. Downgrades are cheap; late upgrades are not.
- Percentages need interpretation. A 5xx rate just above 2% spread thinly across traffic is degradation for some merchants (SEV2); errors concentrated so that most checkout attempts fail is SEV1.

## Declaring and communicating

- SEV1: page the engineering manager on-call immediately, get an incident commander assigned, open `#inc-major`, and start a running timeline in the channel.
- SEV2: open `#inc-major` with a short declaration — what is degraded, who is affected, current mitigation.
- SEV3 and SEV4: handle them in the relevant team channel (`#oncall-platform`, `#team-payments`, `#team-ledger`) or as a normal ticket. `#inc-major` is reserved for SEV1 and SEV2.

## Changing severity

Severity is not fixed at declaration. Upgrade when impact spreads — for example, a queue backlog stops draining and charges start failing widely — and downgrade once mitigation has restored normal behavior for the affected merchants. Post every change in the incident channel with a one-line justification.

## Postmortem requirement

Every SEV1 and SEV2 requires a postmortem within 5 business days of resolution, regardless of how quickly the incident was mitigated. SEV3 and SEV4 do not require one; a short note in the team channel is enough when there are lessons worth keeping.

## Related runbooks

- `oncall-escalation-policy.md` — paging and escalation mechanics behind these levels
- `postmortem-process.md` — the SEV1/SEV2 follow-up process
- `checkout-api-high-error-rate.md`, `payments-worker-queue-backlog.md` — runbooks for the most common SEV1/SEV2 drivers
- `postgres-replica-lag.md`, `redis-memory-pressure.md` — typically SEV2 while degradation is limited to a subset
