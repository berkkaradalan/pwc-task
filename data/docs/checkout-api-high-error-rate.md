---
title: Checkout API High Error Rate Runbook
service: checkout-api
owner_team: team-payments
last_reviewed: 2026-10-01
---

## Summary

This runbook guides on-call engineers through diagnosing and mitigating elevated 5xx error rates on `checkout-api`. In the Quillbridge architecture, `checkout-api` is a Go service running in Kubernetes namespace `prod`. It is owned by `team-payments` and is responsible for synchronous checkout handling and payment creation. A sustained elevation in 5xx HTTP responses indicates that merchant checkout flows are failing, directly impacting transaction volume and merchant operations across the platform.

## Alert / Symptoms

This runbook is activated when Alertmanager sends a page to PagerDuty for the following threshold:
- `checkout-api` 5xx rate > 2% for 5 minutes

Observable symptoms include:
- High HTTP 5xx status codes (500 Internal Server Error, 502 Bad Gateway, 503 Service Unavailable, 504 Gateway Timeout) on the `checkout-api` Grafana dashboard.
- Spike in error logs within Loki associated with `checkout-api` pods.
- Alertmanager firing `CheckoutApiHighErrorRate`.
- PromQL alerting expression:
  ```promql
  sum(rate(http_requests_total{namespace="prod", job="checkout-api", status=~"5.."}[5m]))
  /
  sum(rate(http_requests_total{namespace="prod", job="checkout-api"}[5m]))
  * 100 > 2
  ```

## Impact

Elevated error rates on `checkout-api` disrupt customer transactions across Quillbridge merchants:
- **SEV1**: Checkout is down or payments are failing for most merchants. Buyers cannot complete orders and platform revenue is blocked.
- **SEV2**: Degraded checkout or payment creation for a subset of merchants.
- **SEV3**: Internal non-critical checkout errors with no direct merchant impact.

## Diagnosis

Follow these diagnostic steps to identify the root cause:

1. **Check Pod Health in Kubernetes:**
   Inspect the status of all `checkout-api` pods in the `prod` namespace:
   ```bash
   kubectl get pods -n prod -l app=checkout-api
   ```
   Check for pod restarts, crash loops, or OOMKilled events:
   ```bash
   kubectl describe pods -n prod -l app=checkout-api
   ```

2. **Inspect Application Logs in Loki:**
   Filter logs in Loki or query recent logs directly from Kubernetes:
   ```bash
   kubectl logs -n prod -l app=checkout-api --tail=200
   ```
   Look for common failure patterns:
   - Database connection timeouts or pool exhaustion.
   - Redis connectivity errors or timeouts.
   - Failures when publishing messages to RabbitMQ.

3. **Correlate with Recent Argo CD Deployments:**
   Check whether a recent deployment or sync occurred for the `checkout-api` application in Argo CD:
   - Review the Argo CD dashboard for the `checkout-api` application.
   - Check if an error rate spike aligns with a new release commit or image tag.

4. **Verify Upstream and Downstream Dependencies:**
   - **`api-gateway`**: Verify if Nginx is returning 502/504 due to unreachable backend pods or socket exhaustion.
   - **PostgreSQL 16 & PgBouncer**: Check if PgBouncer waiting clients > 50 for 3 minutes.
     ```bash
     psql -h pg-main-0 -p 6432 -U pgbouncer -c "SHOW POOLS;"
     ```
     Ensure connection pool limits (100 per service) are not saturated.
   - **Redis 7 (`redis-cache`)**: Verify if Redis memory usage > 90% of maxmemory (4 GB), which can reject session and idempotency writes.
   - **RabbitMQ 3.13 (`rabbitmq-0`)**: Ensure `checkout-api` can publish payment events to the `payments.charge` queue.

5. **Examine HTTP Status Breakdown:**
   Run the following PromQL query to isolate specific error codes:
   ```promql
   sum by (status) (rate(http_requests_total{namespace="prod", job="checkout-api", status=~"5.."}[5m]))
   ```

## Mitigation

Execute the appropriate mitigation based on the diagnosis:

1. **Rollback Recent Deployment:**
   If the error spike correlates with a recent release, roll back the application immediately via Argo CD:
   - Open Argo CD, navigate to the `checkout-api` application, and roll back to the previously known good revision.
   - Alternatively, revert the commit in the deployment repository to trigger an automated sync.

2. **Scale Pod Replicas:**
   If pods are experiencing resource saturation or high load, increase pod replicas:
   ```bash
   kubectl scale deployment checkout-api -n prod --replicas=10
   ```

3. **Restart Degraded Pods:**
   If pods are locked, hung on zombie connections, or leaking resources:
   ```bash
   kubectl rollout restart deployment checkout-api -n prod
   ```
   Monitor rollout progress:
   ```bash
   kubectl rollout status deployment checkout-api -n prod
   ```

4. **Mitigate Infrastructure Bottlenecks:**
   - If PgBouncer pool is exhausted, address slow transactions or restart hung worker connections per the database runbook.
   - If Redis 7 (`redis-cache`) exceeds 90% memory utilization, evict non-critical keys or resolve memory leaks per the Redis runbook.

5. **Verify Recovery:**
   Ensure the 5xx error rate drops below 2% on the Grafana dashboard for at least 5 minutes before considering the incident stabilized.

## Escalation

- **Primary On-Call**: Must acknowledge the PagerDuty alert within 5 minutes.
- **Secondary On-Call**: Automatically paged if the alert is not acknowledged after 15 minutes.
- **SEV1 Escalation**: If checkout is down or payments fail for most merchants:
  - Page the engineering manager on-call immediately.
  - Assign an Incident Commander (IC).
  - Open an incident response channel in Slack: `#inc-major`.
- **Team Communications**:
  - Keep `#team-payments` informed of all status changes.
  - If infrastructure, networking, or database issues are identified, notify `#oncall-platform` or `#team-ledger`.
- **Post-Incident**: Every SEV1 and SEV2 incident requires a formal postmortem within 5 business days.

## Related runbooks

- `checkout-api-high-latency.md`
- `deploy-rollback-procedure.md`
- `oncall-escalation-policy.md`
- `severity-levels.md`
- `postmortem-process.md`
- `postgres-connection-pool-exhausted.md`
- `redis-memory-pressure.md`
