---
title: Notification Service Delivery Failures Runbook
service: notification-service
owner_team: team-platform
last_reviewed: 2026-10-01
---

## Summary

This runbook guides on-call engineers in resolving notification delivery failures in `notification-service`. The `notification-service` is a Python application running in Kubernetes namespace `prod` and owned by `team-platform`. It is responsible for sending transactional emails and dispatching webhook notifications to Quillbridge merchants when payment and checkout events occur. Delivery failures prevent merchants from receiving real-time order updates, automated fulfillment triggers, and customer payment receipts.

## Alert / Symptoms

This runbook is triggered when Alertmanager pages the on-call engineer via PagerDuty for elevated notification delivery failure rates.

Observable symptoms include:
- A spike in delivery error rates visible on the `notification-service` Grafana dashboard.
- Increased rate of webhook dispatch retries or email delivery failures in Prometheus metrics:
  ```promql
  sum(rate(notification_delivery_failures_total{namespace="prod", job="notification-service"}[5m]))
  ```
- Error logs in Loki indicating connection timeouts, HTTP 5xx responses from merchant webhook destinations, or transport exceptions from email providers.
- Merchant inquiries reporting missing webhook notifications or delayed buyer email confirmations.

## Impact

Delivery failures impair communication between Quillbridge and merchant backends:
- **SEV1**: Platform-wide notification collapse cascading into checkout or payment failures for most merchants.
- **SEV2**: Degraded delivery or webhook failures impacting a subset of merchants, causing downstream fulfillment delays or order discrepancies.
- **SEV3**: Internal delivery retries or non-critical notification delays with no direct merchant impact.
- **SEV4**: Minor cosmetic template issues or low-risk delays for non-essential notifications.

## Diagnosis

Follow these diagnostic steps to identify the root cause of delivery failures:

1. **Check Pod Health in Kubernetes:**
   Inspect the status of `notification-service` pods in the `prod` namespace:
   ```bash
   kubectl get pods -n prod -l app=notification-service
   ```
   Check for pod crashes, restarts, or resource evictions:
   ```bash
   kubectl describe pods -n prod -l app=notification-service
   ```

2. **Inspect Logs in Loki:**
   Review application logs in Loki or query recent pod logs directly:
   ```bash
   kubectl logs -n prod -l app=notification-service --tail=200
   ```
   Identify the primary failure category:
   - **Merchant Webhook Failures**: Destination servers returning HTTP 4xx/5xx or timing out.
   - **Email Dispatch Failures**: Upstream email provider timeouts, connection drops, or authentication errors.
   - **Internal Service Failures**: Inability to read event data from PostgreSQL 16 or connect to Redis 7.

3. **Determine Scope of Failures:**
   - Check if failures are isolated to one or two specific merchant webhook URLs (indicating merchant endpoint downtime) or if all webhooks and emails across the platform are failing.
   - Examine error metrics grouped by failure type on the `notification-service` Grafana dashboard.

4. **Verify Database and Cache Dependencies:**
   - **PostgreSQL 16 & PgBouncer**: `notification-service` connects through PgBouncer (pool size 100 per service). Check if PgBouncer waiting clients > 50 for 3 minutes, which blocks database reads on `pg-main-0`.
   - **Redis 7 (`redis-cache`)**: If Redis is used for idempotency keys or delivery deduplication, check if memory usage exceeds 90% of maxmemory (4 GB).

5. **Review Recent Deployments in Argo CD:**
   Check the Argo CD application `notification-service` to determine if a recent deployment introduced breaking changes in webhook payloads, worker concurrency, or delivery retry logic.

## Mitigation

Execute the appropriate mitigation steps:

1. **Roll Back Faulty Release:**
   If delivery failures started immediately following a deployment:
   - Navigate to the `notification-service` application in Argo CD.
   - Revert to the previous known good deployment revision.
   - Confirm via the `notification-service` Grafana dashboard that delivery error rates decline.

2. **Scale Notification Service Pods:**
   If worker pods are overwhelmed by notification volume or backlogged due to slow merchant webhook endpoints:
   ```bash
   kubectl scale deployment notification-service -n prod --replicas=8
   ```

3. **Restart Hung Pods:**
   If worker processes have hung connection sockets or degraded event loops:
   ```bash
   kubectl rollout restart deployment notification-service -n prod
   ```
   Track rollout progress:
   ```bash
   kubectl rollout status deployment notification-service -n prod
   ```

4. **Handle Isolated Merchant Webhook Outages:**
   If a single merchant endpoint is timing out and blocking delivery queues:
   - Ensure the notification worker applies backoff retry mechanisms rather than stalling worker threads.
   - Coordinate with merchant support to notify the affected merchant regarding their endpoint health.

5. **Mitigate Infrastructure Contention:**
   - If PgBouncer pool limits or database locks are blocking queries, refer to the PostgreSQL connection pool runbook.
   - If `redis-cache` memory is saturated, follow the Redis memory pressure runbook.

## Escalation

- **Primary On-Call**: Must acknowledge the PagerDuty alert within 5 minutes.
- **Secondary On-Call**: Paged at 15 minutes if the primary does not acknowledge.
- **SEV1 / SEV2 Escalation**:
  - If failures degrade order fulfillment for a subset of merchants (SEV2) or most merchants (SEV1), escalate to Slack channel `#inc-major`.
  - For SEV1 incidents, page the engineering manager on-call immediately and assign an Incident Commander.
- **Team Communications**:
  - Keep `#oncall-platform` updated, as `team-platform` owns this service.
  - If notification delivery issues stem from checkout or payment events, notify `#team-payments`.
- **Post-Incident**: Every SEV1 and SEV2 incident requires an incident postmortem within 5 business days.

## Related runbooks

- `deploy-rollback-procedure.md`
- `oncall-escalation-policy.md`
- `severity-levels.md`
- `postmortem-process.md`
- `postgres-connection-pool-exhausted.md`
- `redis-memory-pressure.md`
