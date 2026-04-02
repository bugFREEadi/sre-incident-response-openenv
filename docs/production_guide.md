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
