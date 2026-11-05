---
title: Postmortem Process
service: all
owner_team: team-platform
last_reviewed: 2026-10-01
---

## When a postmortem is required

Every SEV1 and SEV2 incident requires a postmortem within 5 business days of resolution. This is not optional and not conditional on how short or how well-handled the incident was — small incidents often produce the best process fixes. SEV3 and SEV4 do not require a postmortem; a short write-up in the team channel is enough if there is something worth recording.

## Ownership and timeline

- SEV1: the assigned incident commander owns the postmortem.
- SEV2: an engineer on the team that owns the primarily affected service writes it — the owning team is listed in the front matter of each service runbook.
- Postmortems are Markdown documents. Start drafting while the incident channel is still open: the `#inc-major` log is the raw material for the timeline. Post the draft in `#inc-major` and the relevant team channel (`#team-payments`, `#team-ledger`, `#oncall-platform`) for comment before finalizing.

## Blameless by default

Postmortems assume everyone involved acted reasonably given the information, tooling, and alerts they had. Name systems, alerts, dashboards, and decisions — not people. "The on-call did not know X" is a documentation or alerting gap, not a personal failure.

## Required content

Keep the structure consistent so incidents can be compared over time:

1. **Summary** — two or three sentences: what broke, who was affected, how long it lasted.
2. **Severity and impact** — the SEV level, merchant impact, and duration. Be concrete: "checkout-api 5xx rate above 2% for 37 minutes" rather than "checkout was degraded".
3. **Timeline** — from first signal to full resolution, with times pulled from the incident channel and the PagerDuty acknowledgement history.
4. **Detection** — which alert fired and whether it fired at the right time. If the checkout-api p99 page (> 800 ms for 10 minutes) arrived only after merchants noticed, that is a detection gap to fix.
5. **Root cause and contributing factors** — the technical cause, and why the conditions that allowed it existed.
6. **What went well / what did not** — including how escalation and communication worked against the on-call policy.
7. **Action items** — each with a single owner and a due date. Vague items ("improve monitoring") are not acceptable; "add an alert for X at threshold Y" is.

## Closing the loop

Action items are tracked to completion, not just written down. If mitigation involved a rollback, link the rollback procedure so the next reader knows how the incident was actually stopped. If the incident revealed a missing or stale runbook, writing or updating that runbook is an action item in itself.

## Related runbooks

- `severity-levels.md` — deciding the SEV level recorded in the postmortem
- `oncall-escalation-policy.md` — acknowledgement and escalation expectations referenced in the timeline
- `deploy-rollback-procedure.md` — linked whenever a rollback was the mitigation
- `checkout-api-high-error-rate.md`, `postgres-connection-pool-exhausted.md`, `node-disk-pressure.txt` — examples of runbooks an incident may reveal as missing or in need of update
