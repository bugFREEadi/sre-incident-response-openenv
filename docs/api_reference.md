# API Reference

This document summarizes the public HTTP surface exposed by [server/app.py](server/app.py).

## Base Endpoints

### `GET /`

Browser-facing landing page for the environment.

### `GET /health`

Returns runtime health for validators and operators.

Example response:

```json
{
  "status": "healthy"
}
```

### `GET /metadata`

Returns benchmark metadata such as name, description, and version.

### `GET /schema`

Returns the typed action, observation, and state schemas for the OpenEnv environment.

### `GET /docs`

Interactive OpenAPI documentation for both simulator and ops routes.

### `GET /openapi.json`

OpenAPI schema for the full HTTP contract.

## Simulator API

These routes are the benchmark-facing OpenEnv surface.

### `POST /reset`

Starts a new incident episode.

Request body:

```json
{
  "scenario_id": "s01_restart_cascade"
}
```

Response shape:

```json
{
  "observation": {
    "episode_id": "s01_restart_cascade-1234abcd",
    "scenario_id": "s01_restart_cascade",
    "scenario_name": "Restart Makes It Worse",
    "tick": 0,
    "budget_remaining": 15.0,
    "services": [],
    "alerts": [],
    "recent_logs": [],
    "deploy_history": [],
    "score_so_far": {},
    "available_actions": []
  },
  "reward": 0.0,
  "done": false
}
```

### `POST /step`

Applies one action to an existing episode.

Request body:

```json
{
  "episode_id": "s01_restart_cascade-1234abcd",
  "action": {
    "action_type": "inspect_logs",
    "service": "orders-postgres",
    "tail_n": 20
  }
}
```

### `GET /state`

Returns current state for an episode.

Query params:

- `episode_id`

## Ops Control Plane

All `/ops/v1/*` routes expect:

- `Authorization: Bearer <token>`
- `X-Tenant-Id: <tenant>`

Tenant access is enforced against the token’s `allowed_tenants`.

## Ops Status

### `GET /ops/v1/status`

Returns current execution mode, configured backends, drill status, and persistence backend.

## Read-Only Telemetry

### `GET /ops/v1/logs`

Query params:

- `service`
- `tail_n`

### `GET /ops/v1/metrics`

Query params:

- `service`
- `lookback_minutes`

### `GET /ops/v1/deploy-history`

Query params:

- `service`
- `limit`

### `GET /ops/v1/topology`

Query params:

- `service` optional

## Advisory And Approval Workflow

### `POST /ops/v1/advisories/preview`

Previews a mutating action against guardrails and policies.

Example request:

```json
{
  "incident_id": "inc-2026-0042",
  "action": {
    "action_type": "rollback_service",
    "service": "invoice-consumer",
    "target_version": "2026.03.7"
  },
  "justification": "Deploy correlates with saturation spike",
  "evidence": [
    "orders-postgres connection slots exhausted",
    "invoice-consumer deployment finished 12 minutes ago"
  ]
}
```

### `POST /ops/v1/approvals`

Creates an approval request for a mutating action.

### `GET /ops/v1/approvals/{approval_id}`

Reads one approval record.

### `POST /ops/v1/approvals/{approval_id}/approve`

Approves a pending request.

### `POST /ops/v1/approvals/{approval_id}/reject`

Rejects a pending request.

## Execution

### `POST /ops/v1/actions/execute`

Executes an approved action through the remediation adapter.

Notes:

- blocked in `advisory_only`
- requires approval
- records execution metadata
- supports `dry_run`
- can verify a webhook operation when status polling is configured

### `GET /ops/v1/executions/{execution_id}`

Returns the stored execution record.

## Audit And Backups

### `GET /ops/v1/audit`

Returns recent tenant-scoped audit events.

Query params:

- `limit`

### `GET /ops/v1/admin/backup`

Exports a tenant-scoped backup bundle including:

- approvals
- audit events
- drill results
- execution records
- current execution mode

## Drills And Mode Changes

### `POST /ops/v1/drills/run`

Runs internal drills against the benchmark scenarios and stores the result.

### `GET /ops/v1/drills/latest`

Returns the most recent stored drill run for the tenant.

### `POST /ops/v1/mode`

Changes execution mode.

Important behavior:

- moving away from `advisory_only` requires a recent passing drill when the drill gate is enabled

## Auth Model

Roles used by the control plane:

- `viewer`
- `operator`
- `approver`
- `admin`

Typical permission split:

- `viewer`: status and read-only telemetry
- `operator`: advisory preview, approval request, execution lookup
- `approver`: approve or reject, review audit
- `admin`: drills, mode changes, backup export

## Error Semantics

Common status codes:

- `401`: missing or invalid bearer token
- `403`: tenant mismatch or missing role
- `404`: resource not found
- `409`: invalid workflow transition, expired approval, or blocked mode change
- `429`: privileged route rate limit exceeded
- `503`: adapter not configured
