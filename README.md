---
title: SRE Incident Response OpenEnv
emoji: 🚨
colorFrom: red
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
tags:
  - openenv
  - sre
  - incident-response
  - reinforcement-learning
  - benchmark
license: bsd-3-clause
---

# SRE Incident Response OpenEnv Benchmark

[![OpenEnv Compliance](https://img.shields.io/badge/OpenEnv-Certified-green.svg)](https://github.com/meta-pytorch/OpenEnv)

## Environment Overview and Motivation

**SRE Incident Response** is a high-fidelity reinforcement learning environment designed to evaluate AI agents on causal incident diagnosis and safe remediation in microservice architectures.

### Why This Benchmark?
Traditional RL environments often reward agents for any action that restores metrics to a "green" state. In real-world Site Reliability Engineering (SRE), brute-force remediations (like blind restarts) can exacerbate cascading failures. 

This environment implements **"Tempting Wrong Moves"**:
- **Causal Depth**: Symptoms (e.g., frontend latency) are often decoupled from root causes (e.g., a downstream connection leak).
- **Split Rewards**: The grading system separately scores **Recovery Quality** (SLO restoration) and **Decision Quality** (investigation hygiene). An agent that brute-forces a fix without diagnosis will receive a low final score.
- **Micro-animations and Jitter**: Realistic metric noise represents non-deterministic monitoring, forcing agents to look for statistical significance in logs and metrics before acting.

---

## Task Descriptions and Difficulty Levels

The environment provides five distinct production-style incident scenarios, ranging from straightforward rollbacks to complex cascading failures.

| Task ID | Scenario Name | Difficulty | Key Challenge |
| :--- | :--- | :--- | :--- |
| `s03_wrong_rollback` | Auth Bad Deploy | **Easy** | Failure surfaces in `accounts-api`, but evidence points to `identity-service`. |
| `s02_corrupt_scaleup` | Pricing Corruption | **Medium** | Latency looks like load; scaling masks the bug while corruption continues. |
| `s05_retry_storm` | Webhook Storm | **Medium** | Queue pressure suggests under-provisioning; correct fix is traffic containment. |
| `s04_cache_stampede` | Cache-Key Regression | **Hard** | Looks like a Redis capacity issue, but is actually a key-generation bug in the caller. |
| `s01_restart_cascade`| Connection Leak | **Hard** | **The Hard Benchmark.** Blind restarts of the sick API worsen the database bottleneck. |

---

## Action Space

All actions follow the OpenEnv typed specification. Inspections are cheaper than remediations to encourage diagnostic hygiene.

### Inspection Actions (Cost: 0.5)
- `inspect_logs(service: str, tail_n: int)` - Sample recent stdout/stderr.
- `inspect_metrics(service: str, window_ticks: int)` - View recent p99 latency, error rates, and saturation.
- `inspect_dependencies(service: str)` - Discover service topology and downstream health.

### Remediation Actions (Cost: 1.0)
- `restart_service(service: str)` - Immediate pod restart.
- `rollback_service(service: str, target_version: str)` - Revert to a stable image tag.
- `scale_service(service: str, replicas: int)` - Horizontal pod autoscaling.
- `set_rate_limit(service: str, rps: int)` - Apply traffic shedding/containment.

### Completion Actions (Cost: 0.0)
- `declare_root_cause(service: str, reason_code: str)` - **Mandatory** before finishing.
- `finish_incident()` - Submits the incident for final grading.

---

## Observation Space

Every step returns a rich Pydantic-validated observation containing:
- **Service Snapshots**: p99/p95 latency, error rates (0.0 - 1.0), saturation, and replica counts.
- **Incident Evidence**: Tail of logs containing concrete failure clues and recent deployment/feature-flag events.
- **Alert Stream**: Real-time SLO violation alerts (Critical/Warning).
- **Metadata**: Tick count, budget remaining, and running score hints.

---

## Setup and Usage Instructions

### Local Development
1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
2. **Run the Server**:
   ```bash
   python3 -m uvicorn server.app:app --host 0.0.0.0 --port 8000
   ```
3. **Validate Compliance**:
   ```bash
   openenv validate .
   ```

### Hardware Requirements
- **vCPU**: 2 (Recommended)
- **RAM**: 8 GB
- **OS**: Linux (Containerized)

### Docker Execution
```bash
docker build -t sre-incident-env .
docker run -p 8000:8000 sre-incident-env
```

---

## Baseline Performance Scores

Evaluated using `inference.py` with OpenAI GPT-4o-mini and deterministic fallback policies.

| Scenario | Recovery Score | Decision Score | **Final Score** |
| :--- | :--- | :--- | :--- |
| `s03_wrong_rollback` | 0.85 | 0.65 | **0.75** |
| `s02_corrupt_scaleup` | 0.82 | 0.70 | **0.76** |
| `s01_restart_cascade` | 0.92 | 0.75 | **0.83** |
| **All Tasks Average** | **0.86** | **0.70** | **0.78** |

> [!IMPORTANT]
> **Reproducibility Note:** Naive agents that ignore logs and blind-restart services typically score **< 0.20** on Hard tasks, demonstrating the benchmark's ability to separate noise from causal reasoning.

---

## Submission Guidelines Enforcement
- Root-level `inference.py` ensures strict compliance.
- Emits required `[START]`, `[STEP]`, and `[END]` logging format.
- Uses standard environment variables: `API_BASE_URL`, `MODEL_NAME`, and `HF_TOKEN`.
