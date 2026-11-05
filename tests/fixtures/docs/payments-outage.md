---
title: Payments API outage
service: payments-api
owner_team: payments-sre
last_reviewed: 2026-09-15
---
# Payments API outage

Use this runbook when the payments API error rate is above 5% for 5 minutes.

## Diagnosis

Check the dashboard and the recent deploys.

```bash
# This comment is not a heading.
kubectl -n payments get pods
```

## Mitigation

### Rollback

Roll back to the previous release with the deploy tool.

### Scale up

Increase the replica count if the pods are CPU-bound.
