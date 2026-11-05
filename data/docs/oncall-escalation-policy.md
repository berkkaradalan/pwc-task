---
title: On-Call Escalation Policy
service: all
owner_team: team-platform
last_reviewed: 2026-10-01
---

## Purpose

This policy describes how alerts reach the on-call engineer and what happens when a page is not acknowledged. It applies to every production service in the `prod` namespace: `api-gateway`, `checkout-api`, `payments-worker`, `ledger-service`, and `notification-service`.

## How pages flow

Prometheus evaluates the alert rules against platform metrics. When a rule fires, Alertmanager routes a page to PagerDuty, and PagerDuty pages the primary on-call. There is no email-only tier: if something pages, it is considered actionable and a human is expected to respond.

## Acknowledge within 5 minutes

The primary on-call must acknowledge a page within 5 minutes. Acknowledge as soon as you see the page, even if you have not diagnosed anything yet. Acknowledging means "I am on it", not "this is fixed" — but an unacknowledged page starts the escalation clock, so a fast ack is the single most useful on-call habit.

If you genuinely cannot work the page (no laptop access, traveling), ack if you can and immediately post in `#oncall-platform` asking someone to take over. Do not let a page sit untouched because you hope it resolves itself.

## Missed acknowledgement: secondary paged at 15 minutes

If the primary has not acknowledged within 15 minutes, the secondary on-call is paged automatically. If you are the secondary and receive that page, treat the primary as unreachable and start working the incident. Post in `#oncall-platform` so a late-arriving primary does not duplicate your work, and hand the incident back explicitly once you are both online.

## SEV1 escalation

For SEV1 incidents — checkout down or payments failing for most merchants — two things happen immediately at declaration:

1. The engineering manager on-call is paged.
2. An incident commander is assigned.

Do not wait for the engineering manager or the incident commander to respond before starting mitigation. The primary on-call keeps driving mitigation while coordination is set up around them.

Declare SEV1 early when the picture is bad. If the checkout-api 5xx page fires (5xx rate above 2% for 5 minutes) and the Grafana dashboard for checkout-api shows errors still climbing, or merchants broadly cannot complete checkout, declare SEV1 rather than waiting for certainty. Over-declaring costs a bit of process; under-declaring costs merchant trust.

## Slack channels and what belongs where

- `#inc-major` — the incident channel for SEV1 and SEV2. Post the declaration, key findings, mitigation steps, and the all-clear here. Keep decisions in the channel, not in DMs, so the postmortem has a record to work from.
- `#oncall-platform` — coordination with the platform on-call: handoffs, second pairs of eyes, and the fallback channel when paging appears broken.
- `#team-payments` — loop in the payments team for `checkout-api` or `payments-worker` issues that need deeper service knowledge than you have.
- `#team-ledger` — loop in the ledger team for `ledger-service` or PostgreSQL problems, for example if ledger records look inconsistent during an incident.

When asking a team channel for help, include the alert name, the current metric values from the Grafana dashboard (one dashboard per service, named after the service), and what you have already tried.

## If paging appears broken

If a page fires and nobody acknowledges within the normal window, or a manual page produces no PagerDuty activity, post in `#oncall-platform` immediately and ask another engineer to verify the page in PagerDuty. Do not assume silence means someone else is handling it.

## Related runbooks

- `severity-levels.md` — deciding SEV1 through SEV4
- `postmortem-process.md` — required follow-up for every SEV1 and SEV2
- `checkout-api-high-error-rate.md`, `checkout-api-high-latency.md` — the two checkout-api pages
- `postgres-connection-pool-exhausted.md`, `redis-memory-pressure.md` — infrastructure pages the platform on-call commonly works
