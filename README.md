---
title: SRE Incident Response OpenEnv
emoji: 🚨
colorFrom: red
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
license: bsd-3-clause
---

# SRE Incident Response

An incident-response environment where the locally obvious remediation often worsens global system state, so agents must infer the true causal fault domain before acting.

**SRE Incident Response** is an OpenEnv environment for training and evaluating agents on causal incident diagnosis and safe staged remediation in simulated microservice systems. Unlike environments that reward any action sequence that restores service health, this benchmark scores both recovery quality and decision quality. Agents that brute-force restarts receive low scores even when a service looks temporarily better, because the grader separately penalizes unnecessary actions, blast-radius violations, and incorrect root-cause declarations.

## Why It Is Hard

Each scenario is designed around three constraints:

- There is a tempting wrong move that looks correct from surface metrics.
- There is at least one investigative clue in logs, metrics, dependencies, or deploy history.
- The grader separates recovery from reasoning, so "restart until green" underperforms careful diagnosis plus minimal intervention.

The killer case is Scenario 1: `payments-api` looks sick, but restarting it makes the system worse because the hidden root cause is an `invoice-consumer` connection leak exhausting `orders-postgres`.

## Production Guide

For a full implementation and integration guide, see [docs/production_guide.md](/Volumes/macSSD/git/meta-hackathon-sst/docs/production_guide.md).

That guide covers:

- how the simulator maps to real incident-response workflows
- how to call the environment over HTTP and with the Python client
- how to integrate it into an incident agent, ChatOps bot, or internal control plane
- how to adapt the benchmark to your own service graph and failure modes
- what this environment should and should not automate in real production settings

## Repository Layout

```text
.
├── actions.py                    # core simulation/action executor
├── models.py                     # simulator dataclasses
├── observation.py                # partial observations
├── reward.py                     # dense reward + final score logic
├── verifier.py                   # deterministic grader
├── world.py                      # hidden world state engine
├── openenv.yaml
├── pyproject.toml
├── inference.py
├── scenarios/
│   ├── base.py
│   ├── s01_restart_cascade.py
│   ├── s02_corrupt_scaleup.py
│   └── s03_wrong_rollback.py
├── sre_incident_env/
│   ├── models.py                 # OpenEnv Action/Observation/State models
│   └── client.py                 # OpenEnv EnvClient
├── server/
│   ├── app.py                    # OpenEnv FastAPI entrypoint
│   └── sre_incident_environment.py
├── scripts/
│   └── validate_policies.py
├── tests/
│   └── test_scenarios.py
└── Dockerfile
```

## Scenario Set

| Scenario | Hidden root cause | Tempting wrong move | Correct remediation |
| --- | --- | --- | --- |
| `s01_restart_cascade` | `invoice-consumer` `2026.04.1` connection leak | Restart `payments-api` | Declare `invoice-consumer` + `connection_leak`, then roll back to `2026.03.7` |
| `s02_corrupt_scaleup` | `ff_dynamic_pricing` corruption in `checkout-api` | Scale `checkout-api` | Declare `checkout-api` + `feature_flag_corruption`, then `set_rate_limit(..., 0)` |
| `s03_wrong_rollback` | `identity-service` `2026.04.0` bad deploy | Roll back `accounts-api` | Declare `identity-service` + `bad_deploy`, then roll back to `2026.03.6` |
| `s04_cache_stampede` | `catalog-api` `2026.04.5` cache-key regression | Scale `catalog-api` or restart `redis-catalog` | Declare `catalog-api` + `cache_key_regression`, then roll back to `2026.03.9` |
| `s05_webhook_retry_storm` | `notification-dispatcher` duplicate-send flag rollout | Scale `notification-dispatcher` | Declare `notification-dispatcher` + `duplicate_dispatch`, then `set_rate_limit(..., 0)` |

## Action Space

Inspection actions cost `0.5` budget units:

- `inspect_logs(service, tail_n=20)`
- `inspect_metrics(service, window_ticks=5)`
- `inspect_dependencies(service)`

Remediation actions cost `1.0` budget unit:

- `restart_service(service)`
- `rollback_service(service, target_version)`
- `scale_service(service, replicas)`
- `set_rate_limit(service, rps)`

Completion actions cost `0`:

- `declare_root_cause(service, reason_code)`
- `finish_incident()`

`declare_root_cause(...)` must be called before `finish_incident()`.

## Observation Space

Every observation contains:

- episode metadata: `episode_id`, `scenario_id`, `scenario_name`, `tick`, `budget_remaining`
- service snapshots: status, version, latency, error rate, saturation, replicas, dependency statuses
- incident evidence: sampled recent logs, active alerts, recent deploy history
- live scoring hints: `score_so_far`
- standard OpenEnv fields: `reward`, `done`, `metadata`

## Scoring

The environment reports:

- `recovery_score`: whether SLOs are restored, how quickly, and with how little residual degradation.
- `decision_score`: whether the agent declared the right fault domain and avoided shotgun remediation.
- `final_score = 0.5 * recovery_score + 0.5 * decision_score`

This split is the core benchmark property. Recovery without reasoning should not win.

## Tasks And Difficulty

- `s01_restart_cascade` is the hard benchmark task. The symptom is front-door latency, but the real fault domain is a downstream connection leak.
- `s02_corrupt_scaleup` is medium. The obvious scale-up move masks corruption rather than fixing it.
- `s03_wrong_rollback` is easy-to-medium. The failure appears in `accounts-api`, but the evidence points to `identity-service`.
- `s04_cache_stampede` is medium-to-hard. It looks like a cache-capacity problem, but the real issue is a key-generation regression in the caller.
- `s05_webhook_retry_storm` is medium. It looks like queue pressure that wants more workers, but the right move is to contain duplicated traffic.

## Production Flavor

The environment now uses a more production-like SaaS stack instead of abstract toy services:

- edge and synchronous APIs: `payments-api`, `checkout-api`, `accounts-api`
- background workers: `invoice-consumer`
- stateful dependencies: `orders-postgres`, `customer-profile-db`, `session-redis`
- supporting platform services: `pricing-engine`, `payments-gateway`, `identity-service`

The clues are also written in production-style telemetry:

- rollout events look like ArgoCD or LaunchDarkly activity
- logs reference concrete endpoints, pods, request IDs, and database/application names
- failure modes mirror common real incidents such as connection leaks, bad auth rollouts, and feature-flagged pricing regressions

## OpenEnv Compliance

The repo now includes the required OpenEnv packaging pieces:

- [openenv.yaml](/Volumes/macSSD/git/meta-hackathon-sst/openenv.yaml)
- [pyproject.toml](/Volumes/macSSD/git/meta-hackathon-sst/pyproject.toml)
- [server/app.py](/Volumes/macSSD/git/meta-hackathon-sst/server/app.py)
- [sre_incident_env/models.py](/Volumes/macSSD/git/meta-hackathon-sst/sre_incident_env/models.py)
- [sre_incident_env/client.py](/Volumes/macSSD/git/meta-hackathon-sst/sre_incident_env/client.py)
- [inference.py](/Volumes/macSSD/git/meta-hackathon-sst/inference.py)

Validation commands:

```bash
openenv validate .
python3 -m uvicorn server.app:app --host 127.0.0.1 --port 8000
openenv validate --url http://127.0.0.1:8000
```

## Local Validation

The repo includes a gate test for Scenario 1:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_policies.py
```

Current validation output for Scenario 1:

- Naive restart loop: `final_score = 0.1854`
- Correct diagnosis + rollback: `final_score = 0.8333`

That satisfies the intended benchmark separation: the obvious wrong move scores far below the correct causal fix path.

OpenEnv validation status:

- local package validation: passes
- runtime validation against a live server: passes

## Baseline Inference

The required root-level [inference.py](/Volumes/macSSD/git/meta-hackathon-sst/inference.py) uses the OpenAI client, reads `API_BASE_URL`, `MODEL_NAME`, `OPENAI_API_KEY` and `HF_TOKEN`, emits `[START]`, `[STEP]`, and `[END]` logs, and falls back to deterministic safe policies if model output is malformed.

Dry-run baseline with fallback policies currently produces:

- `s01_restart_cascade`: `0.8333`
- `s02_corrupt_scaleup`: `0.7583`
- `s03_wrong_rollback`: `0.7500`
- `s04_cache_stampede`: `0.8250`
- `s05_webhook_retry_storm`: `0.8250`
- average: `0.7983`

## Running The API

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Run the server:

```bash
python3 -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Endpoints:

- `POST /reset`
- `POST /step`
- `GET /state`
- `GET /health`
- `GET /schema`
- `GET /metadata`
- `POST /mcp`

Example flow:

```bash
curl -X POST http://127.0.0.1:8000/reset \
  -H 'content-type: application/json' \
  -d '{"scenario_id":"s01_restart_cascade"}'

curl -X POST http://127.0.0.1:8000/step \
  -H 'content-type: application/json' \
  -d '{
    "episode_id":"<episode-id>",
      "action":{
      "action_type":"inspect_logs",
      "service":"orders-postgres",
      "tail_n":20
    }
  }'
```

## Real-World Usage

The safest way to use this environment in a real app is as an evaluation harness for an incident agent before that agent is allowed to touch live infrastructure.

In practice that usually means:

1. Build an agent that can reason over logs, metrics, deploy history, and dependencies.
2. Point that agent at this environment first and measure whether it investigates before acting, declares the correct fault domain, and avoids shotgun remediation.
3. Reuse the same tool contract in production, but map simulator actions to real systems such as Prometheus, Loki, ArgoCD, Kubernetes, or your feature-flag service.
4. Start with advisory mode in production, where the agent proposes actions for human approval.
5. Only allow constrained automation after the agent consistently performs well in this benchmark and in internal rehearsals.

The detailed rollout pattern, API examples, and integration code live in [docs/production_guide.md](/Volumes/macSSD/git/meta-hackathon-sst/docs/production_guide.md).

## Notes

- The environment is deterministic at the simulation layer; observation sampling uses seeded noise from the episode id and tick.
- The implementation intentionally stays small: 4 services per scenario, 5 incident families, 8 typed actions, and deterministic grading.
- The benchmark now ships as a real OpenEnv package instead of only a custom FastAPI app.
- `openenv validate .` and `openenv validate --url ...` both pass locally.
- `docker build .` could not be exercised in this session because the local Docker daemon socket was unavailable, even though the CLI itself is installed.
