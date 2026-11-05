---
title: Payments Worker Queue Backlog Runbook
service: payments-worker
owner_team: team-payments
last_reviewed: 2026-10-01
---

## Summary

This runbook provides diagnostic and mitigation steps for resolving a queue backlog in `payments-worker`. The `payments-worker` service is a Python application running in Kubernetes namespace `prod` and owned by `team-payments`. It consumes messages from the RabbitMQ 3.13 queue `payments.charge` hosted on `rabbitmq-0` and executes payment requests against the card processor. When messages accumulate in `payments.charge`, downstream payment settlements, authorization confirmations, and order processing are delayed across the Quillbridge platform.

## Alert / Symptoms

This runbook is triggered by an Alertmanager notification to PagerDuty under the following condition:
- `payments.charge` queue depth > 10,000 messages

Observable symptoms include:
- Queue depth metric rising above 10,000 messages on the `payments-worker` Grafana dashboard.
- Alert firing in Prometheus:
  ```promql
  rabbitmq_queue_messages{queue="payments.charge", namespace="prod"} > 10000
  ```
- RabbitMQ CLI reporting high message depth and low consumption rate:
  ```bash
  kubectl exec -it rabbitmq-0 -n prod -- rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers
  ```
- Delayed charge completions reported by merchants.

## Impact

A backlog in `payments.charge` creates a processing delay between checkout creation and payment completion:
- **SEV1**: Payments are failing or stalled for most merchants across the platform.
- **SEV2**: Payment processing is delayed or degraded for a subset of merchants.
- **SEV3**: Queue accumulation during off-peak processing that causes internal delays without impacting merchant checkout or customer notifications.

## Diagnosis

Investigate the root cause using the following sequence:

1. **Verify Consumer Count and Queue State on RabbitMQ:**
   Connect to `rabbitmq-0` and inspect the `payments.charge` queue:
   ```bash
   kubectl exec -it rabbitmq-0 -n prod -- rabbitmqctl list_queues name messages consumers
   ```
   Check whether the consumer count has dropped to zero or is significantly lower than the active pod count of `payments-worker`.

2. **Check `payments-worker` Pod Status in Kubernetes:**
   Inspect worker pods in the `prod` namespace:
   ```bash
   kubectl get pods -n prod -l app=payments-worker
   ```
   Look for pods in `CrashLoopBackOff`, `OOMKilled`, or `Error` states:
   ```bash
   kubectl describe pods -n prod -l app=payments-worker
   ```

3. **Inspect Application Logs in Loki:**
   Retrieve recent logs from the Python workers to identify processing exceptions or blocking calls:
   ```bash
   kubectl logs -n prod -l app=payments-worker --tail=200
   ```
   Key indicators to search for:
   - Network timeouts or error responses when calling the card processor.
   - Unhandled Python exceptions causing message re-queuing loops.
   - Database connection timeouts to `pg-main-0` via PgBouncer.
   - Failures communicating with `ledger-service` or writing double-entry records.

4. **Review Recent Deployments in Argo CD:**
   Check the Argo CD application `payments-worker` to see if a recent deployment introduced code regressions, increased per-message processing latency, or misconfigured consumer concurrency.

5. **Check Downstream Dependencies:**
   - **Card Processor**: Check if the external card processor is experiencing elevated latency, rate limiting, or an outage.
   - **PgBouncer / PostgreSQL 16**: Ensure PgBouncer waiting clients is not > 50 for 3 minutes, which could block worker transaction commits.
   - **RabbitMQ 3.13 (`rabbitmq-0`)**: Ensure the message broker itself is not under memory or disk alarm.

## Mitigation

Apply the following remediation steps according to the findings:

1. **Scale Up Worker Pods:**
   If the workers are healthy but processing rate is lagging behind an incoming surge of charges:
   ```bash
   kubectl scale deployment payments-worker -n prod --replicas=15
   ```
   Observe if consumer count increases and queue depth begins trending downward on the Grafana dashboard.

2. **Roll Back Problematic Release:**
   If a recent deploy to Argo CD application `payments-worker` introduced crashes or slow processing loops:
   - Roll back the application in Argo CD to the previous working revision.
   - Monitor pod recreation in `prod`:
     ```bash
     kubectl rollout status deployment payments-worker -n prod
     ```

3. **Restart Hung Worker Processes:**
   If worker threads are locked on stalled network sockets to the card processor without timing out:
   ```bash
   kubectl rollout restart deployment payments-worker -n prod
   ```

4. **Address Downstream Card Processor Outage:**
   If the external card processor is returning errors or timing out:
   - Pause or rate-limit retry logic to avoid queue churning.
   - Notify on-call leadership immediately to coordinate merchant communications.

5. **Verify Queue Drain:**
   Track the queue depth on `rabbitmq-0` until `payments.charge` drops well below 10,000 messages and normal processing velocity is restored.

## Escalation

- **Primary On-Call**: Must acknowledge the PagerDuty alert within 5 minutes.
- **Secondary On-Call**: Paged at 15 minutes if unacknowledged by the primary.
- **SEV1 Escalation**: If payments are failing or stalled for most merchants:
  - Page the engineering manager on-call immediately.
  - Assign an Incident Commander.
  - Coordinate the active response in Slack channel `#inc-major`.
- **Team Channels**:
  - Keep `#team-payments` informed on worker and queue status.
  - If RabbitMQ cluster or Kubernetes nodes require intervention, loop in `#oncall-platform`.
  - If double-entry bookkeeping writes fail, engage `#team-ledger`.
- **Post-Incident**: All SEV1 and SEV2 incidents require an incident postmortem within 5 business days.

## Related runbooks

- `checkout-api-high-error-rate.md`
- `checkout-api-high-latency.md`
- `deploy-rollback-procedure.md`
- `oncall-escalation-policy.md`
- `severity-levels.md`
- `postmortem-process.md`
- `postgres-connection-pool-exhausted.md`
