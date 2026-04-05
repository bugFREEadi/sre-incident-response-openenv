# Deployment Guide

This guide explains how to deploy **SRE Incident Response OpenEnv** locally, in Docker, and on Hugging Face Spaces.

## Deployment Modes

The repository supports two practical modes:

- benchmark-only deployment
- benchmark plus ops control plane deployment

The same server binary supports both. The difference is whether the ops environment variables are configured.

## Local Development

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Run the service:

```bash
python3 -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Recommended validation:

```bash
openenv validate .
openenv validate --url http://127.0.0.1:8000
python3 -m unittest discover -s tests -v
```

For local control-plane development without bearer tokens:

```bash
export OPS_DISABLE_AUTH_FOR_LOCAL_DEV=true
```

## Docker

Build:

```bash
docker build -t sre-incident-response .
```

Run:

```bash
docker run --rm -p 8000:8000 \
  -e OPS_DISABLE_AUTH_FOR_LOCAL_DEV=true \
  sre-incident-response
```

## Hugging Face Spaces

This repo is already packaged as a Docker Space.

Important files:

- [Dockerfile](Dockerfile)
- [openenv.yaml](openenv.yaml)

Typical deployment flow:

```bash
hf upload <space-id> . --type space --commit-message "Deploy latest SRE Incident Response"
```

After deploy:

```bash
curl https://<space-subdomain>.hf.space/health
openenv validate --url https://<space-subdomain>.hf.space
```

## Persistence Choices

### SQLite

Default:

```bash
export OPS_DATABASE_PATH=data/ops_control_plane.sqlite3
```

Good for:

- local development
- single-node pilots
- simple demos

### Postgres

Recommended for more serious deployments:

```bash
export OPS_DATABASE_URL=postgresql://user:pass@db.internal:5432/sre_ops
```

Good for:

- multi-instance deployments
- durable shared state
- tenant-scoped operational history

## Secrets

The control plane supports three secret formats:

- raw values
- `file:///path/to/secret`
- `env://ENV_VAR_NAME`

Example:

```bash
export OPS_PROMETHEUS_BEARER_TOKEN=file:///var/run/secrets/prometheus_token
export OPS_REMEDIATION_BEARER_TOKEN=env://REMEDIATION_TOKEN
```

## Example Production Configuration

```bash
export OPS_REQUIRE_AUTH=true
export OPS_EXECUTION_MODE=advisory_only
export OPS_DRILL_GATE_ENABLED=true
export OPS_ALLOWED_SERVICES=payments-api,invoice-consumer,checkout-api
export OPS_ALLOWED_MUTATING_ACTIONS=restart_service,rollback_service,scale_service,set_rate_limit
export OPS_DATABASE_URL=postgresql://user:pass@db.internal:5432/sre_ops
export OPS_AUDIT_JSONL_PATH=/var/log/sre-incident/audit.jsonl
export OPS_LOKI_BASE_URL=https://loki.internal.example.com
export OPS_PROMETHEUS_BASE_URL=https://prometheus.internal.example.com
export OPS_ARGOCD_BASE_URL=https://argocd.internal.example.com
export OPS_REMEDIATION_WEBHOOK_URL=https://automation-gateway.internal.example.com/remediate
export OPS_REMEDIATION_STATUS_URL_TEMPLATE=https://automation-gateway.internal.example.com/operations/{operation_id}
```

## Token Configuration

```bash
export OPS_API_TOKENS_JSON='{
  "viewer-token": {
    "actor_id": "viewer",
    "roles": ["viewer"],
    "allowed_tenants": ["default"]
  },
  "operator-token": {
    "actor_id": "operator",
    "roles": ["operator"],
    "allowed_tenants": ["default"]
  },
  "approver-token": {
    "actor_id": "incident-commander",
    "roles": ["approver"],
    "allowed_tenants": ["default"]
  },
  "admin-token": {
    "actor_id": "platform-admin",
    "roles": ["admin"],
    "allowed_tenants": ["default"]
  }
}'
```

## Rollout Recommendation

### Phase 1

Deploy benchmark only:

- use `/reset`, `/step`, `/state`
- validate scores
- benchmark agent behavior

### Phase 2

Enable read-only ops adapters:

- logs
- metrics
- deploy history
- topology

Keep:

- `OPS_EXECUTION_MODE=advisory_only`

### Phase 3

Enable approval workflow:

- advisory previews
- approval requests
- audit review
- drills

Still keep execution disabled until your drill bar is stable.

### Phase 4

Enable controlled execution:

- configure remediation webhook
- configure status polling
- move to `approval_required`
- keep admin and approver rate limits in place

## Operational Checks

Before allowing real execution:

- OpenEnv validation passes
- tests pass
- adapters can reach backend systems
- token and tenant model is correct
- drill runs pass consistently
- backup export works
- audit logs are being persisted

## Recommended Reverse Proxy Controls

For stronger production posture, place the service behind:

- TLS termination
- IP allowlists or VPN access
- centralized auth if you replace bearer tokens
- distributed rate limiting
- request logging
- WAF rules for privileged routes

## Disaster Recovery

At minimum:

- use Postgres instead of sqlite for important environments
- persist audit JSONL to durable storage if you rely on it
- test `/ops/v1/admin/backup`
- snapshot the database regularly
- document how to rotate adapter credentials and tokens
