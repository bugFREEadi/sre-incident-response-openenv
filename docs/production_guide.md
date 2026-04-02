# Production Guide

This document explains how to use **SRE Incident Response OpenEnv** as a production-grade benchmark, rehearsal tool, and pre-deployment safety harness for AI incident agents.

It is intentionally written from the perspective of a real engineering team, not just a hackathon reviewer.

## What This Environment Is

This repository implements a deterministic incident-response simulator exposed through the OpenEnv HTTP contract.

At a high level:

- `POST /reset` starts an incident scenario and returns the initial observation.
- `POST /step` accepts one typed action and advances the world by one tick.
- `GET /state` returns the current episode state.
- `GET /health`, `GET /metadata`, and `GET /schema` support validation and tooling.
- `GET /ops/v1/*` and `POST /ops/v1/*` expose the production-safety control plane.

The environment is designed around a production truth that many agents get wrong:

- the service with the loudest symptom is often not the root cause
- the first obvious remediation is often the wrong one
- a locally useful action can worsen the global system state

This benchmark exists to evaluate whether an agent can reason causally under uncertainty before it is trusted with real operator workflows.

## What This Environment Is Not

This environment is not:

- a full Kubernetes simulator
- a real infrastructure control plane
- a direct production automation system
- a replacement for observability, runbooks, or human on-call ownership

It should be used as:

- an offline benchmark
- a prompt and policy evaluation harness
- a rehearsal system for incident agents
- a safe stand-in for live production tools

## Production-Safety Control Plane

This repository now includes an ops control plane layered onto the same FastAPI server.

Implemented capabilities:

- read-only adapters for logs, metrics, deploy history, and topology
- advisory previews for mutating actions
- approval requests and explicit approve or reject flows
- bearer-token authentication with role checks
- tenant-aware audit logs and approval persistence
- sqlite by default and Postgres when `OPS_DATABASE_URL` is set
- service and action allowlists
- action guardrails for scale and traffic limits
- policy rules for tenant, role, action, service, and UTC time-window enforcement
- execution records and verification-aware remediation orchestration
- admin backup export and rate-limited privileged routes
- drill-gated mode changes before enabling automation

This gives you a practical bridge between:

- a pure benchmark
- a read-only incident assistant
- a tightly controlled remediation workflow

### Execution Modes

The control plane supports three modes:

- `advisory_only`
  - default mode
  - mutations cannot be executed
  - agents can preview actions and request approvals
- `approval_required`
  - approved actions can be executed through the remediation adapter
  - intended for human-in-the-loop operations
- `enabled`
  - still approval-gated in this implementation
  - intended for carefully automated workflows after drills pass

The mode cannot move out of `advisory_only` unless a recent passing drill run exists when the drill gate is enabled.

## Mental Model

Think of this environment as a flight simulator for AI incident response.

In a real company, an incident agent might eventually receive:

- logs from Loki, ELK, Datadog, or CloudWatch
- metrics from Prometheus, Grafana, Datadog, or New Relic
- deploy history from ArgoCD, GitHub Actions, Spinnaker, or internal deploy systems
- feature-flag changes from LaunchDarkly or internal flag platforms
- topology and service ownership from internal service catalogs

In this environment, those are simulated as:

- recent logs
- alert objects
- service snapshots
- dependency statuses
- deploy history
- a hidden world state that evolves after each action

The goal is not to memorize a puzzle. The goal is to evaluate whether an agent behaves like a careful on-call engineer.

## How The Runtime Works

### Reset

`reset()` starts a fresh episode for one scenario and returns the initial observation.

Internally, this happens in [server/sre_incident_environment.py](/Volumes/macSSD/git/meta-hackathon-sst/server/sre_incident_environment.py) and [world.py](/Volumes/macSSD/git/meta-hackathon-sst/world.py):

- a scenario is selected
- a hidden `WorldState` is created
- alerts are refreshed
- a partial observation is built
- the OpenEnv wrapper returns the typed `SREIncidentObservation`

### Step

Each `step()` call does exactly one thing:

1. Validate the typed action.
2. Apply the action using the simulator action executor.
3. Advance the incident world by one tick.
4. Recompute observation and live score.
5. Return the next observation, reward, and done flag.

That one-action-per-step discipline matters because it forces explicit tradeoffs. Agents cannot "inspect everything and remediate everything" in one turn.

### State

`state()` exposes current session state for tooling and orchestration.

It is useful for:

- debugging an agent run
- verifying whether an episode is terminated
- reading remaining budget
- inspecting what root cause the agent has declared

### Finish

The agent must call:

1. `declare_root_cause(service, reason_code)`
2. `finish_incident()`

This is a deliberate design choice. It lets the verifier distinguish:

- recovery quality
- decision quality

Without that separation, an agent that blindly restarts or rate-limits services could appear stronger than it really is.

## Ops API Surface

Beyond the OpenEnv simulator routes, the server now exposes production control-plane routes:

- `GET /ops/v1/status`
- `GET /ops/v1/logs`
- `GET /ops/v1/metrics`
- `GET /ops/v1/deploy-history`
- `GET /ops/v1/topology`
- `POST /ops/v1/advisories/preview`
- `POST /ops/v1/approvals`
- `GET /ops/v1/approvals/{approval_id}`
- `POST /ops/v1/approvals/{approval_id}/approve`
- `POST /ops/v1/approvals/{approval_id}/reject`
- `POST /ops/v1/actions/execute`
- `GET /ops/v1/audit`
- `POST /ops/v1/drills/run`
- `GET /ops/v1/drills/latest`
- `POST /ops/v1/mode`

These routes live in [server/app.py](/Volumes/macSSD/git/meta-hackathon-sst/server/app.py) and are backed by [server/ops_service.py](/Volumes/macSSD/git/meta-hackathon-sst/server/ops_service.py).

## Action Contract

The typed action model lives in [sre_incident_env/models.py](/Volumes/macSSD/git/meta-hackathon-sst/sre_incident_env/models.py).

Allowed `action_type` values:

- `inspect_logs`
- `inspect_metrics`
- `inspect_dependencies`
- `restart_service`
- `rollback_service`
- `scale_service`
- `set_rate_limit`
- `declare_root_cause`
- `finish_incident`

### Inspection Actions

Inspection actions cost `0.5` budget units.

They are intentionally cheap but not free. This creates realistic tension:

- fully blind action is unsafe
- infinitely cautious inspection is too expensive

Inspection actions:

- `inspect_logs(service, tail_n=20)`
- `inspect_metrics(service, window_ticks=5)`
- `inspect_dependencies(service)`

### Remediation Actions

Remediation actions cost `1.0` budget unit.

They mutate the world and may improve or worsen the incident depending on whether the agent acted on the correct fault domain.

Remediation actions:

- `restart_service(service)`
- `rollback_service(service, target_version)`
- `scale_service(service, replicas)`
- `set_rate_limit(service, rps)`

### Completion Actions

Completion actions cost `0`.

They are:

- `declare_root_cause(service, reason_code)`
- `finish_incident()`

If `finish_incident()` is called before a root cause declaration, the environment returns an error and the episode continues.

## Observation Contract

The typed observation model also lives in [sre_incident_env/models.py](/Volumes/macSSD/git/meta-hackathon-sst/sre_incident_env/models.py).

Each observation includes:

- `episode_id`
- `scenario_id`
- `scenario_name`
- `tick`
- `budget_remaining`
- `services`
- `alerts`
- `recent_logs`
- `deploy_history`
- `score_so_far`
- `available_actions`

### Why The Observation Is Partial

The environment never exposes hidden ground truth directly.

That means:

- the real root cause is hidden
- some services may look healthy even when they are the source of failure
- symptoms can appear upstream while the cause lives downstream

This is how production incidents work. Observability surfaces evidence, not truth.

## Scoring Model

The environment returns live reward each step and a final verifier-backed score at episode completion.

### Recovery Score

Recovery measures whether the system actually got better:

- were violated SLOs restored
- how quickly did recovery happen
- how much residual degradation remains

### Decision Score

Decision quality measures whether the agent reasoned correctly:

- did it declare the correct root-cause service
- did it declare the correct root-cause type
- did it investigate before acting
- did it avoid unnecessary remediations
- did it avoid repeated shotgun actions
- did it avoid collateral blast radius

### Why This Matters

In real operations, a superficially good action can be strategically bad.

Example:

- restarting the front-door API may briefly clear a queue
- the database pool leak remains
- global system health degrades again a few ticks later

A benchmark that only rewards temporary symptom reduction teaches the wrong lesson. This environment does not.

## Scenario Inventory

### `s01_restart_cascade`

Production-style story:

- `payments-api` is slow and erroring
- `orders-postgres` is saturated
- `invoice-consumer` looks mostly healthy
- a recent deploy introduced a connection leak

Why it matters:

- this tests whether the agent can resist restarting the visibly degraded front door
- the correct move is to infer the downstream cause and roll back the leaking consumer

### `s02_corrupt_scaleup`

Production-style story:

- `checkout-api` latency rises
- a pricing-related feature flag changed recently
- retries inflate latency and hide corruption

Why it matters:

- this tests whether the agent notices correctness failures, not just throughput symptoms
- scaling the service masks the problem but does not remove the bad write pattern

### `s03_wrong_rollback`

Production-style story:

- `accounts-api` is failing authenticated requests
- the visible symptom looks like a user-service failure
- the actual breakage is in `identity-service`

Why it matters:

- this tests cross-service diagnosis and deploy-history reasoning

### `s04_cache_stampede`

Production-style story:

- `catalog-api` and `redis-catalog` are under pressure
- it looks like cache capacity or Redis instability
- the true issue is a cache-key regression in the caller

Why it matters:

- this tests whether the agent understands cause versus saturation symptom

### `s05_webhook_retry_storm`

Production-style story:

- queue pressure rises in notification delivery
- the natural instinct is to scale workers
- the real issue is duplicated dispatch traffic

Why it matters:

- this tests containment behavior, not just capacity reflexes

## How To Run It Locally

### Install Dependencies

```bash
python3 -m pip install -r requirements.txt
```

### Start The Server

```bash
python3 -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

### Validate OpenEnv Compliance

```bash
openenv validate .
openenv validate --url http://127.0.0.1:8000
```

### Run Tests

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_policies.py
```

## How To Use It Over HTTP

### Start A Scenario

```bash
curl -X POST http://127.0.0.1:8000/reset \
  -H 'Content-Type: application/json' \
  -d '{"scenario_id":"s01_restart_cascade"}'
```

Example response shape:

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
    "available_actions": [
      "inspect_logs",
      "inspect_metrics",
      "inspect_dependencies",
      "restart_service",
      "rollback_service",
      "scale_service",
      "set_rate_limit",
      "declare_root_cause",
      "finish_incident"
    ]
  },
  "reward": 0.0,
  "done": false
}
```

### Take An Inspection Step

```bash
curl -X POST http://127.0.0.1:8000/step \
  -H 'Content-Type: application/json' \
  -d '{
    "episode_id": "s01_restart_cascade-1234abcd",
    "action": {
      "action_type": "inspect_logs",
      "service": "orders-postgres",
      "tail_n": 20
    }
  }'
```

### Declare Root Cause

```bash
curl -X POST http://127.0.0.1:8000/step \
  -H 'Content-Type: application/json' \
  -d '{
    "episode_id": "s01_restart_cascade-1234abcd",
    "action": {
      "action_type": "declare_root_cause",
      "service": "invoice-consumer",
      "reason_code": "connection_leak"
    }
  }'
```

### Apply Remediation

```bash
curl -X POST http://127.0.0.1:8000/step \
  -H 'Content-Type: application/json' \
  -d '{
    "episode_id": "s01_restart_cascade-1234abcd",
    "action": {
      "action_type": "rollback_service",
      "service": "invoice-consumer",
      "target_version": "2026.03.7"
    }
  }'
```

### Finish The Incident

```bash
curl -X POST http://127.0.0.1:8000/step \
  -H 'Content-Type: application/json' \
  -d '{
    "episode_id": "s01_restart_cascade-1234abcd",
    "action": {
      "action_type": "finish_incident"
    }
  }'
```

### Inspect State Mid-Episode

```bash
curl http://127.0.0.1:8000/state?episode_id=s01_restart_cascade-1234abcd
```

## How To Use The Ops Control Plane

### 1. Configure Auth

The control plane expects bearer tokens defined through `OPS_API_TOKENS_JSON`.

Example:

```bash
export OPS_API_TOKENS_JSON='{
  "viewer-token": {"actor_id": "viewer", "roles": ["viewer"]},
  "operator-token": {"actor_id": "oncall-operator", "roles": ["operator"]},
  "approver-token": {"actor_id": "incident-commander", "roles": ["approver"]},
  "admin-token": {"actor_id": "platform-admin", "roles": ["admin"]}
}'
```

Role intent:

- `viewer`: read-only telemetry and status
- `operator`: advisory previews, approval requests, execution attempts
- `approver`: approve or reject actions, review audit history
- `admin`: run drills and change execution mode

Tenant isolation:

- every control-plane request is scoped by `X-Tenant-Id`
- tokens can only access tenants listed in `allowed_tenants`
- approvals, audit events, drill results, executions, and settings are stored per tenant

### 2. Configure Read-Only Adapters

Supported adapter configuration:

#### Loki logs

```bash
export OPS_LOKI_BASE_URL=https://loki.internal.example.com
export OPS_LOKI_BEARER_TOKEN=...
export OPS_LOKI_QUERY_TEMPLATE='{service="{service}"}'
```

#### Prometheus metrics

```bash
export OPS_PROMETHEUS_BASE_URL=https://prometheus.internal.example.com
export OPS_PROMETHEUS_BEARER_TOKEN=...
export OPS_PROMETHEUS_QUERY_TEMPLATE='up{job="{service}"}'
```

#### ArgoCD deploy history

```bash
export OPS_ARGOCD_BASE_URL=https://argocd.internal.example.com
export OPS_ARGOCD_BEARER_TOKEN=...
```

#### Topology

Use either a static file:

```bash
export OPS_TOPOLOGY_FILE=/etc/sre/topology.json
```

or a generic HTTP endpoint:

```bash
export OPS_TOPOLOGY_URL=https://service-catalog.internal.example.com/topology
export OPS_TOPOLOGY_BEARER_TOKEN=...
```

### 3. Keep The System In Advisory Mode First

Default mode:

```bash
export OPS_EXECUTION_MODE=advisory_only
```

In this mode:

- the agent can read telemetry
- the agent can preview a mutating action
- the agent can request approval
- the system will refuse actual execution

### 4. Preview A Mutating Action

```bash
curl -X POST http://127.0.0.1:8000/ops/v1/advisories/preview \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer operator-token' \
  -d '{
    "incident_id": "inc-2026-0042",
    "action": {
      "action_type": "rollback_service",
      "service": "invoice-consumer",
      "target_version": "2026.03.7"
    },
    "justification": "Recent deploy correlates with DB connection leak",
    "evidence": [
      "orders-postgres connection slots exhausted",
      "invoice-consumer deployed 12 minutes ago"
    ]
  }'
```

The response tells you:

- whether the action is allowlisted
- whether it violates scale or rate-limit guardrails
- whether approval is required
- what execution mode the system is currently in

### 5. Request Approval

```bash
curl -X POST http://127.0.0.1:8000/ops/v1/approvals \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer operator-token' \
  -d '{
    "incident_id": "inc-2026-0042",
    "action": {
      "action_type": "rollback_service",
      "service": "invoice-consumer",
      "target_version": "2026.03.7"
    },
    "justification": "Evidence points to recent deploy causing pool exhaustion",
    "evidence": [
      "postgres log shows application_name=invoice-consumer",
      "consumer metrics diverged after deploy"
    ]
  }'
```

### 6. Approve Or Reject

Approve:

```bash
curl -X POST http://127.0.0.1:8000/ops/v1/approvals/<approval-id>/approve \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer approver-token' \
  -d '{"note":"Approved by incident commander"}'
```

Reject:

```bash
curl -X POST http://127.0.0.1:8000/ops/v1/approvals/<approval-id>/reject \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer approver-token' \
  -d '{"note":"Need stronger evidence before mutating prod"}'
```

### 7. Run Internal Drills Before Enabling Automation

The control plane enforces a passing drill before mode changes when `OPS_DRILL_GATE_ENABLED=true`.

Run a drill:

```bash
curl -X POST http://127.0.0.1:8000/ops/v1/drills/run \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer admin-token' \
  -d '{
    "strategy": "safe_fallback",
    "minimum_average_score": 0.70,
    "minimum_scenario_score": 0.60
  }'
```

This uses the benchmark scenarios as internal readiness drills and records:

- per-scenario score
- recovery score
- decision score
- whether the drill run passed

### 8. Move To Approval-Required Mode

After a passing drill:

```bash
curl -X POST http://127.0.0.1:8000/ops/v1/mode \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer admin-token' \
  -d '{"execution_mode":"approval_required"}'
```

Without a recent passing drill, the server will refuse this transition.

### 9. Execute An Approved Action

The remediation path is intentionally adapter-based.

This implementation ships with a stronger generic webhook executor:

```bash
export OPS_REMEDIATION_WEBHOOK_URL=https://automation-gateway.internal.example.com/remediate
export OPS_REMEDIATION_BEARER_TOKEN=...
export OPS_REMEDIATION_STATUS_URL_TEMPLATE=https://automation-gateway.internal.example.com/operations/{operation_id}
export OPS_REMEDIATION_VERIFY_ATTEMPTS=5
export OPS_REMEDIATION_VERIFY_DELAY_SECONDS=1
```

Execution behavior:

- an idempotency key is attached to the remediation request
- the adapter expects an `operation_id`
- if a status URL template is configured, the service polls for terminal status
- execution records are stored and can be read later

Execute:

```bash
curl -X POST http://127.0.0.1:8000/ops/v1/actions/execute \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer operator-token' \
  -d '{
    "incident_id": "inc-2026-0042",
    "approval_id": "<approval-id>",
    "action": {
      "action_type": "rollback_service",
      "service": "invoice-consumer",
      "target_version": "2026.03.7"
    },
    "dry_run": false
  }'
```

If the system is still in `advisory_only`, this request is rejected even with a valid approval.

### 10. Review Audit Logs

```bash
curl http://127.0.0.1:8000/ops/v1/audit \
  -H 'Authorization: Bearer approver-token'
```

Audit events are persisted in:

- sqlite at `OPS_DATABASE_PATH`
- or Postgres at `OPS_DATABASE_URL`
- newline-delimited JSON at `OPS_AUDIT_JSONL_PATH` when configured

### 11. Export A Tenant Backup Bundle

```bash
curl http://127.0.0.1:8000/ops/v1/admin/backup \
  -H 'Authorization: Bearer admin-token' \
  -H 'X-Tenant-Id: default'
```

The backup bundle contains:

- approvals
- audit events
- drill results
- execution records
- current execution mode

## How To Use It With The Python Client

The OpenEnv client wrapper lives in [sre_incident_env/client.py](/Volumes/macSSD/git/meta-hackathon-sst/sre_incident_env/client.py).

Minimal example:

```python
import asyncio

from sre_incident_env import SREIncidentAction, SREIncidentEnv


async def main() -> None:
    async with SREIncidentEnv(base_url="http://127.0.0.1:8000") as env:
        result = await env.reset(scenario_id="s01_restart_cascade")

        result = await env.step(
            SREIncidentAction(
                action_type="inspect_logs",
                service="orders-postgres",
                tail_n=20,
            )
        )

        result = await env.step(
            SREIncidentAction(
                action_type="declare_root_cause",
                service="invoice-consumer",
                reason_code="connection_leak",
            )
        )

        result = await env.step(
            SREIncidentAction(
                action_type="rollback_service",
                service="invoice-consumer",
                target_version="2026.03.7",
            )
        )

        result = await env.step(SREIncidentAction(action_type="finish_incident"))
        print(result.reward, result.done, result.observation.score_so_far)


asyncio.run(main())
```

## How To Build An Incident Agent Against It

The right way to use this environment with an agent is to keep the policy loop simple and explicit.

Recommended loop:

1. Reset into a scenario.
2. Read the observation.
3. Ask the model for exactly one typed action.
4. Execute the action.
5. Append the action and result to history.
6. Repeat until done.

Important agent design rules:

- never let the model emit free-form shell or kubectl commands here
- force typed actions only
- keep action selection single-step
- log every step and reward
- preserve full observation history for debugging

The baseline implementation for this loop is [inference.py](/Volumes/macSSD/git/meta-hackathon-sst/inference.py).

## How To Use It In A Real App

The environment should sit next to your real app as a safety and evaluation layer.

### Recommended Adoption Path

#### Stage 1: Offline Benchmarking

Before an agent touches production:

- run it against all benchmark scenarios
- compare score distributions across prompts and models
- inspect failure cases manually
- verify that it does not default to restart loops

At this stage, the environment acts as an internal certification harness.

#### Stage 2: Internal Rehearsal

Adapt scenarios to your own architecture:

- rename services to your real systems
- encode your common failure modes
- mirror your telemetry style
- add your usual feature-flag and deploy metadata

At this stage, the environment becomes a digital twin of your incident patterns.

#### Stage 3: Advisory Mode In Production

Expose production-safe tools to the agent:

- `inspect_logs(service)`
- `inspect_metrics(service)`
- `inspect_dependencies(service)`
- `get_deploy_history(service)`
- `get_flag_changes(service)`

Do not expose destructive actions yet.

Instead, have the agent produce:

- probable root cause
- evidence summary
- proposed remediation
- confidence
- rollback risk

A human operator reviews and approves.

#### Stage 4: Guardrailed Automation

Only after strong benchmark and advisory performance should you automate a narrow subset of actions, for example:

- disable a known-bad feature flag
- roll back a single service within a bounded allowlist
- set traffic to zero for a single service

Still require:

- approval workflows
- audit logging
- service allowlists
- concurrency guards
- rollback protection

### Practical Architecture In A Real Product

Typical control-plane shape:

```text
Slack / PagerDuty / Internal UI
            |
            v
      Incident Agent
            |
    +-------+--------+
    |                |
    v                v
Benchmark Mode   Production Mode
OpenEnv API      Real Tool Adapters
```

Where:

- benchmark mode talks to this environment
- production mode talks to real infrastructure adapters

The key idea is to keep the action contract stable.

For example:

- in benchmark mode, `inspect_metrics("payments-api")` returns simulated metrics
- in production mode, the same conceptual tool queries Prometheus or Datadog

This lets you use the benchmark to test the same policy interface you plan to deploy.

## Example: Mapping Benchmark Actions To Real Systems

This environment defines the decision interface. Your production app would supply the concrete tool implementation.

Suggested mapping:

- `inspect_logs(service)` -> Loki, ELK, Datadog Logs, CloudWatch Logs
- `inspect_metrics(service)` -> Prometheus, Datadog Metrics, Grafana APIs
- `inspect_dependencies(service)` -> service catalog, tracing graph, static dependency graph
- `rollback_service(service, target_version)` -> ArgoCD, Helm, Spinnaker, internal deploy API
- `scale_service(service, replicas)` -> Kubernetes deployment scale API
- `set_rate_limit(service, rps)` -> API gateway, Envoy, NGINX, internal traffic-control API

### Example Adapter Layer

```python
class IncidentTools:
    async def inspect_logs(self, service: str, tail_n: int = 20) -> dict:
        ...

    async def inspect_metrics(self, service: str, window_ticks: int = 5) -> dict:
        ...

    async def inspect_dependencies(self, service: str) -> dict:
        ...

    async def rollback_service(self, service: str, target_version: str) -> dict:
        ...

    async def scale_service(self, service: str, replicas: int) -> dict:
        ...

    async def set_rate_limit(self, service: str, rps: int) -> dict:
        ...
```

Then the policy layer stays unchanged across benchmark and production.

## How To Adapt The Simulator To Your Company

This repository is intentionally small enough to customize.

### Update Service Graphs

Edit the scenario modules under [scenarios/](/Volumes/macSSD/git/meta-hackathon-sst/scenarios) to reflect your own dependency graph.

Good adaptations:

- checkout -> pricing -> payments -> postgres
- auth -> user-service -> session-store
- API gateway -> worker -> Kafka -> warehouse

### Update Evidence

Make logs, alerts, and deploy history resemble your stack:

- include your real service names
- mirror your log wording and severity levels
- use your rollout system naming
- include request IDs, pod names, namespaces, regions, or tenant IDs

### Update Root-Cause Families

Good real-world incident families:

- connection leaks
- thread-pool exhaustion
- retry storms
- feature-flag corruption
- auth token validation regressions
- cache stampedes
- regional dependency degradation

### Keep The Benchmark Honest

Do not make scenarios solvable by superficial pattern matching alone.

Each good scenario should have:

- one tempting wrong move
- one decisive clue
- one correct fault domain
- one remediation that is safe and minimal

## Production Safety Guidelines

If you build a real app on top of this benchmark, keep these guardrails.

### Start Read-Only

First expose only:

- logs
- metrics
- traces
- topology
- deploy history

This lets you evaluate reasoning without giving the agent a blast radius.

### Require Human Approval For Mutations

Before any real rollback or scale change:

- show the evidence chain
- show the predicted blast radius
- show the exact action payload
- log approval and requester identity

This repository now enforces that pattern structurally through:

- approval records
- approval status transitions
- audit events
- execution-mode checks
- drill-gated mode changes

### Keep Actions Typed

Do not let the model improvise raw operational commands in early production systems.

Typed actions are easier to:

- validate
- audit
- rate-limit
- simulate
- replay

### Log Every Decision

Persist:

- input observation
- chosen action
- reward or production outcome
- final root-cause declaration
- operator approval

This repository now also persists:

- tenant-scoped execution records
- execution backend and operation ids
- drill history for automation gating

### Add Policy Rules, Not Just Static Allowlists

The control plane now supports policy rules through `OPS_POLICY_RULES_JSON`.

Supported rule dimensions:

- `action_types`
- `services`
- `tenants`
- `roles`
- UTC active time windows
- `deny`
- `require_approval`
- `max_replicas`
- `max_rps`

Example:

```bash
export OPS_POLICY_RULES_JSON='[
  {
    "rule_id": "restrict-prod-scale",
    "services": ["payments-api"],
    "action_types": ["scale_service"],
    "max_replicas": 4
  },
  {
    "rule_id": "deny-midnight-rollbacks",
    "action_types": ["rollback_service"],
    "active_from_hour_utc": 0,
    "active_to_hour_utc": 5,
    "deny": true
  }
]'
```

### Rate-Limit Privileged APIs

Approver and admin paths are now rate-limited in-process.

Relevant knobs:

- `OPS_ADMIN_RATE_LIMIT_COUNT`
- `OPS_ADMIN_RATE_LIMIT_WINDOW_SECONDS`

For a more complete deployment, place the service behind:

- an API gateway
- mTLS or service mesh policy
- global distributed rate limiting
- centralized auth

This is essential for evaluation, postmortems, and model iteration.

## Suggested Real-World Use Cases

This environment is especially useful for teams building:

- AI on-call copilots
- incident triage assistants
- rollback recommendation systems
- SRE training environments
- runbook-following agents
- internal reliability platforms

It is also useful for:

- prompt evaluation
- model selection
- regression testing after tool or prompt changes
- CI checks for incident-agent behavior

## CI And Release Recommendations

If you want this repo to feel truly production-grade, use the environment in CI:

1. Run unit tests.
2. Run policy validation.
3. Run `openenv validate .`.
4. Run a deterministic baseline over all scenarios.
5. Fail the build if score separation regresses.

Recommended checks:

- Scenario 1 naive path remains below target threshold
- Scenario 1 correct path remains above target threshold
- all scenarios still produce bounded scores in `[0.0, 1.0]`
- inference script remains reproducible

## Hugging Face Space Deployment

This repo is packaged as a Docker Space with [openenv.yaml](/Volumes/macSSD/git/meta-hackathon-sst/openenv.yaml) and [Dockerfile](/Volumes/macSSD/git/meta-hackathon-sst/Dockerfile).

Deployed endpoints:

- `/health`
- `/metadata`
- `/schema`
- `/reset`
- `/step`
- `/state`
- `/mcp`
- `/ops/v1/status`
- `/ops/v1/*`

The Space page is for discoverability and documentation. The `hf.space` domain is what evaluators and automated tooling hit.

## Environment Variables For Inference

The root-level [inference.py](/Volumes/macSSD/git/meta-hackathon-sst/inference.py) expects:

- `API_BASE_URL`
- `MODEL_NAME`
- `HF_TOKEN`

Defaults are intentionally set only for:

- `API_BASE_URL`
- `MODEL_NAME`

`HF_TOKEN` has no default and must be provided at runtime.

All LLM calls use the OpenAI client configured from those variables.

## Control-Plane Environment Variables

Core control-plane flags:

- `OPS_REQUIRE_AUTH`
- `OPS_DISABLE_AUTH_FOR_LOCAL_DEV`
- `OPS_EXECUTION_MODE`
- `OPS_APPROVAL_REQUIRED_FOR_MUTATIONS`
- `OPS_DRILL_GATE_ENABLED`
- `OPS_DRILL_VALIDITY_HOURS`
- `OPS_ALLOWED_SERVICES`
- `OPS_ALLOWED_MUTATING_ACTIONS`
- `OPS_MAX_SCALE_REPLICAS`
- `OPS_MAX_RATE_LIMIT_RPS`
- `OPS_ADMIN_RATE_LIMIT_COUNT`
- `OPS_ADMIN_RATE_LIMIT_WINDOW_SECONDS`
- `OPS_DATABASE_PATH`
- `OPS_DATABASE_URL`
- `OPS_AUDIT_JSONL_PATH`
- `OPS_POLICY_RULES_JSON`

Adapter variables:

- `OPS_LOKI_BASE_URL`
- `OPS_LOKI_BEARER_TOKEN`
- `OPS_LOKI_QUERY_TEMPLATE`
- `OPS_PROMETHEUS_BASE_URL`
- `OPS_PROMETHEUS_BEARER_TOKEN`
- `OPS_PROMETHEUS_QUERY_TEMPLATE`
- `OPS_ARGOCD_BASE_URL`
- `OPS_ARGOCD_BEARER_TOKEN`
- `OPS_TOPOLOGY_FILE`
- `OPS_TOPOLOGY_URL`
- `OPS_TOPOLOGY_BEARER_TOKEN`
- `OPS_REMEDIATION_WEBHOOK_URL`
- `OPS_REMEDIATION_BEARER_TOKEN`
- `OPS_REMEDIATION_STATUS_URL_TEMPLATE`
- `OPS_REMEDIATION_VERIFY_ATTEMPTS`
- `OPS_REMEDIATION_VERIFY_DELAY_SECONDS`

Secret resolution supports:

- plain values
- `file:///path/to/secret`
- `env://ENV_VAR_NAME`

## Common Mistakes

### Mistake 1: Treating It Like A Dashboard

This benchmark is not asking, "Which service is red?"

It is asking, "What is the real causal fault domain, and what is the minimal safe fix?"

### Mistake 2: Over-Automating Too Early

If you jump directly from benchmark wins to production mutation rights, you will create risk.

Use the environment as a gate, not a permission slip.

### Mistake 3: Optimizing Only For Recovery

Temporary symptom relief is not equivalent to good incident handling.

This benchmark intentionally separates those.

### Mistake 4: Adding Too Much Complexity

You do not need a perfect digital twin to get value.

A small number of sharp, realistic, causally tricky scenarios is better than a huge brittle simulator.

## Recommended Next Extensions

If you want to evolve this repository further, the highest-value additions are:

- incident ticket context in the initial observation
- richer deploy metadata such as commit SHA, author, and rollout stage
- SLI naming conventions tied to real products
- trace-like evidence for upstream/downstream causality
- a read-only production adapter layer for advisory mode demos

## Summary

Use this environment the same way responsible teams use staging, chaos drills, and game days:

- benchmark the agent offline
- adapt scenarios to your stack
- rehearse failure modes safely
- ship advisory mode first
- automate only after measured evidence

That is the real-life path from simulator to trustworthy operational tooling.
