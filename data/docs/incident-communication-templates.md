---
title: Incident Communication Templates
service: all
owner_team: team-platform
last_reviewed: 2026-10-01
---

## Purpose

These templates keep incident communication consistent, quick to write, and safe to read. They exist so the on-call engineer under pressure does not have to invent wording, and so the `#inc-major` record reads as a clean timeline the postmortem can be built from. Copy the template, replace every `<...>` placeholder, delete lines that do not apply, and post. Prefer a fast rough update over a late polished one.

## When to use

- **SEV1 and SEV2:** all incident communication happens in `#inc-major`. For SEV1, the engineering manager on-call is paged immediately and an incident commander is assigned; the IC owns the communication cadence, including approval of merchant-facing messages.
- **SEV3 and SEV4:** no incident channel is opened. A clear summary in `#oncall-platform`, `#team-payments`, or `#team-ledger` is enough; the templates are optional.
- **Merchant-facing messages:** delivered by `notification-service` as emails and webhooks to merchants. On-call drafts the wording, the IC approves it for SEV1/SEV2. If notification delivery itself is failing, coordinate per notification-service-delivery-failures.md and use whatever channel still works.
- **Ground rules:** acknowledge the page within 5 minutes before writing anything long. Keep decisions in the channel, not in DMs. Never promise a fix time for a cause Quillbridge does not control. Never edit or delete posted updates — corrections are new updates.
- **After resolution:** the channel timeline feeds the postmortem, due within 5 business days for every SEV1 and SEV2.

## Templates

### SEV1 initial update

> **[SEV1 DECLARED] <one-line title> — <time, CET>**
> **Impact:** <checkout down / payments failing for most merchants> since <time>.
> **Signals:** <alerts firing with values, e.g. checkout-api 5xx rate above 2% for 5 minutes; payments.charge depth above 10,000 messages>
> **IC:** <name>. Engineering manager on-call paged.
> **Currently doing:** <first mitigation action and who is on it>
> **Next update:** <time> or sooner if the picture changes.

### SEV2 initial update

> **[SEV2 DECLARED] <one-line title> — <time, CET>**
> **Impact:** degraded checkout or payments for a subset of merchants — <subset, if known>.
> **Signals:** <alert and current value, e.g. checkout-api p99 above 800 ms for 10 minutes>
> **Owner:** <on-call engineer>. Working theory: <hypothesis or "unknown">.
> **Currently doing:** <action>.
> **Next update:** <time>.

### Progress update (SEV1 and SEV2)

> **[SEV<n> UPDATE <k>] <title> — <time, CET>**
> **Since last update:** <findings and actions taken>.
> **Current impact:** <who is affected right now, with the metric>.
> **Next:** <planned actions with owners>.
> **Next update:** <time>.

If nothing has changed, post that anyway — silence reads as "nobody is looking".

### Resolution (SEV1 and SEV2)

> **[SEV<n> RESOLVED] <title> — <time, CET>**
> **Impact window:** <start> to <end>.
> **What fixed it:** <mitigation that actually worked>.
> **Verification:** <evidence, e.g. checkout-api 5xx under 2% for 15 minutes; payments.charge below 10,000 messages and draining>.
> **Follow-ups:** postmortem due within 5 business days, owner <name>; action items <list or "tracked in the postmortem">.

### Merchant message — external card processor outage

Sent via `notification-service` when the card processor's public status page confirms an outage. Two rules from card-processor-outage.md apply: say plainly that the cause is external, and never promise a resolution time Quillbridge does not control.

> **Subject: Card payments currently delayed**
> Some card payments are currently taking longer than usual to complete. The cause is an outage at our card processing provider, not an issue with your Quillbridge account or integration.
> **What this means for you:** payments you accept may be delayed, or may show as failed and need to be retried. <If applicable: no action is needed — delayed payments complete automatically once the provider recovers.>
> We are monitoring the provider's status page continuously and will send a follow-up as soon as payments return to normal.

Follow-up once the provider recovers, same channel:

> **Subject: Update — card payments recovering**
> Our card processing provider has declared recovery. Delayed payments are now processing again, and we will confirm once all delayed payments have completed.

No internal tool names, team names, or severity labels belong in merchant-facing messages.

## Related runbooks

- oncall-escalation-policy.md
- severity-levels.md
- postmortem-process.md
- card-processor-outage.md
- notification-service-delivery-failures.md
