---
title: RabbitMQ Node Down
service: RabbitMQ
owner_team: team-platform
last_reviewed: 2026-10-01
---

## Summary

Quillbridge runs RabbitMQ 3.13 as a single node, `rabbitmq-0`, in the `prod` namespace. `checkout-api` publishes charge requests to the `payments.charge` queue on it, and `payments-worker` consumes from that queue to execute charges against the card processor. When `rabbitmq-0` is down, both directions stop: new charges cannot be enqueued and queued charges cannot be executed.

There is no second broker to fail over to. Recovery means bringing `rabbitmq-0` itself back, then draining the backlog of charge requests that publishers created while it was gone.

## Alert / Symptoms

There is no dedicated broker-down page. You usually arrive here from the knock-on effects:

- `checkout-api` 5xx rate above 2% for 5 minutes, when publishes to `payments.charge` fail during payment creation.
- `payments-worker` logs in Loki showing AMQP connection errors or consumer cancellations, with zero consumers attached to `payments.charge`.
- Merchants reporting payments that hang at creation or never complete.
- The pod not Running, or `rabbitmqctl status` failing outright:

```bash
kubectl -n prod get pods | grep rabbitmq
kubectl -n prod exec -it rabbitmq-0 -- rabbitmqctl status
```

While the broker is down the queue-depth metric goes stale, so the `payments.charge` depth page (> 10,000 messages) usually fires only after recovery, when publishers reconnect and the backlog arrives at once.

## Impact

**SEV1** if payments are failing or stalled for most merchants. **SEV2** if a brief outage delayed payments for a subset of merchants and client retries absorbed the rest. **SEV3** for a restart with no message loss and no merchant-visible delay. Classify per severity-levels.md based on how long charge execution was stopped.

## Diagnosis

Start with the pod and what Kubernetes knows about it:

```bash
kubectl -n prod get pods -o wide | grep rabbitmq
kubectl -n prod describe pod rabbitmq-0
kubectl get nodes
```

Look for `CrashLoopBackOff`, `OOMKilled`, a `Pending` pod waiting for a volume, or a node problem underneath the broker. Check recent events for the pod and its node:

```bash
kubectl -n prod get events --sort-by=.lastTimestamp | grep -i rabbitmq
kubectl describe node <node> | grep -A5 Conditions
```

Read the broker's own logs, including the previous container if it restarted:

```bash
kubectl -n prod logs rabbitmq-0 --tail=200
kubectl -n prod logs rabbitmq-0 --previous
```

RabbitMQ states its own shutdown reason: a memory or disk alarm on the node, a failed boot, or a kubelet eviction. A disk alarm usually traces back to node disk usage above 85% — follow node-disk-pressure.txt before anything else in that case.

Then confirm the clients are otherwise healthy, so you do not chase a consumer bug as a broker outage:

```bash
kubectl -n prod get pods -l app=payments-worker
kubectl -n prod logs -l app=payments-worker --tail=100
```

If the broker answers `rabbitmqctl status` and the queue still has consumers, you are in the wrong runbook: payments-worker-queue-backlog.md for a slow drain, card-processor-outage.md for charges failing downstream of a healthy broker.

## Mitigation

Do not purge the queue. `rabbitmqctl purge_queue payments.charge` is never the right move, however tempting the depth number looks during recovery. Those messages are real charge instructions from merchants; a backlog is drained by processing it, not by deleting it.

1. If the pod is hung, stuck terminating, or crash-looping without a config change, delete it and let the StatefulSet recreate it:

```bash
kubectl -n prod delete pod rabbitmq-0
kubectl -n prod get pods -w | grep rabbitmq
```

2. Fix the underlying cause before expecting the restart to hold:
   - Disk alarm or node disk above 85%: free space per node-disk-pressure.txt; the broker may keep blocking publishing until the alarm clears.
   - `OOMKilled`: the broker exceeds its memory limit under current queue load — coordinate a limit review in `#oncall-platform`; restarts alone will keep failing.
   - Crash after a configuration change: coordinate the revert with `#oncall-platform` rather than hot-editing the live objects.

3. Do not delete the broker's PersistentVolumeClaim or data directory to "rebuild" it during an incident. That destroys every queued charge message and turns a delay into permanent payment loss.

4. Verify recovery:

```bash
kubectl -n prod exec -it rabbitmq-0 -- rabbitmqctl status
kubectl -n prod exec -it rabbitmq-0 -- rabbitmqctl list_queues name messages messages_ready messages_unacknowledged consumers
```

Consumers should reattach until the count matches the running `payments-worker` pods. Then work payments-worker-queue-backlog.md until `payments.charge` is back below 10,000 messages, and confirm on the `checkout-api` Grafana dashboard that the 5xx rate is under 2% before calling the incident mitigated.

## Escalation

Acknowledge the PagerDuty page within 5 minutes. If it is not acknowledged, the secondary on-call is paged at 15 minutes.

A SEV1 pages the engineering manager on-call immediately and assigns an incident commander. For SEV1 and SEV2, run the incident in the `#inc-major` channel, keep `#oncall-platform` informed, and pull in `#team-payments` for consumer and queue-draining decisions on `payments-worker`. Engage `#team-ledger` if charge records for payments delayed by the outage need review afterwards.

Every SEV1 and SEV2 requires a postmortem within 5 business days.

## Related runbooks

- payments-worker-queue-backlog.md
- card-processor-outage.md
- checkout-api-high-error-rate.md
- node-disk-pressure.txt
- severity-levels.md
- oncall-escalation-policy.md
- postmortem-process.md
