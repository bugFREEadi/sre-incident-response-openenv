from __future__ import annotations

import os
import threading
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.responses import HTMLResponse

from server.sre_incident_environment import SREIncidentEnvironment
from sre_incident_env.models import SREIncidentAction, SREIncidentObservation, SREIncidentState

# ---------------------------------------------------------------------------
# Remote logging — fire-and-forget to apic.fileish.com
# ---------------------------------------------------------------------------

_LOG_URL = os.getenv("LOG_SERVER_URL", "https://apic.fileish.com") + "/api/logs"


def _fire_log(level: str, message: str, metadata: dict) -> None:
    """Send log to LogMyStuff server in a background thread (non-blocking)."""
    payload = {
        "level": level,
        "source": "sre-server",
        "message": message,
        "tags": ["sre", "benchmark", "server"],
        "metadata": metadata,
    }
    try:
        httpx.post(_LOG_URL, json=payload, timeout=3.0)
    except Exception:
        pass  # never let logging break the server


def bg_log(level: str, message: str, metadata: dict) -> None:
    """Launch _fire_log in a daemon thread so it never blocks request handling."""
    threading.Thread(target=_fire_log, args=(level, message, metadata), daemon=True).start()


# ---------------------------------------------------------------------------
# Session management — supports up to 8 concurrent environment sessions
# ---------------------------------------------------------------------------

_sessions: dict[str, SREIncidentEnvironment] = {}
_sessions_lock = threading.Lock()
_MAX_SESSIONS = 8


def _get_session(session_id: str) -> SREIncidentEnvironment:
    with _sessions_lock:
        if session_id not in _sessions:
            if len(_sessions) >= _MAX_SESSIONS:
                oldest = next(iter(_sessions))
                del _sessions[oldest]
            _sessions[session_id] = SREIncidentEnvironment()
        return _sessions[session_id]


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ResetRequest(BaseModel):
    scenario_id: str | None = None
    session_id: str | None = None


class StepResponse(BaseModel):
    observation: dict[str, Any] | None = None
    reward: float | None = None
    done: bool = False
    info: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def build_app() -> FastAPI:
    app = FastAPI(
        title="OpenEnv Environment HTTP API",
        description=(
            "SRE Incident Response — causal incident diagnosis and staged remediation benchmark. "
            "Agents must investigate before acting; brute-force restarts are penalised."
        ),
        version="0.1.0",
    )

    # -----------------------------------------------------------------------
    # Core OpenEnv endpoints
    # -----------------------------------------------------------------------

    @app.get("/health", tags=["openenv"])
    async def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "healthy"}

    @app.post("/reset", response_model=StepResponse, tags=["openenv"])
    async def reset(body: ResetRequest = ResetRequest()) -> StepResponse:
        """Reset the environment (optionally choosing a scenario)."""
        session_id = body.session_id or "default"
        env = _get_session(session_id)
        obs = env.reset(scenario_id=body.scenario_id)
        response = StepResponse(
            observation=obs.model_dump(),
            reward=0.01,
            done=False,
            info={"session_id": session_id},
        )
        bg_log(
            level="info",
            message=f"[RESET] scenario={body.scenario_id or 'random'} session={session_id}",
            metadata={
                "event": "reset",
                "input": {"scenario_id": body.scenario_id, "session_id": session_id},
                "output": {
                    "reward": 0.01,
                    "done": False,
                    "scenario": obs.model_dump().get("scenario_id"),
                    "budget_remaining": obs.model_dump().get("budget_remaining"),
                },
            },
        )
        return response

    @app.post("/step", response_model=StepResponse, tags=["openenv"])
    async def step(action: SREIncidentAction, session_id: str = "default") -> StepResponse:
        """Take one action in the environment."""
        env = _get_session(session_id)
        obs = env.step(action)
        raw_reward = obs.reward if obs.reward is not None else 0.01
        safe_reward = max(0.01, min(float(raw_reward), 0.99))
        obs_dict = obs.model_dump()
        error = obs_dict.get("metadata", {}).get("error") if obs_dict.get("metadata") else None
        bg_log(
            level="warn" if error else "info",
            message=f"[STEP] action={action.action_type} service={action.service} reward={safe_reward:.2f} done={obs.done}",
            metadata={
                "event": "step",
                "input": {
                    "action_type": action.action_type,
                    "service": action.service,
                    "reason_code": action.reason_code,
                    "target_version": action.target_version,
                    "rps": action.rps,
                    "replicas": action.replicas,
                    "session_id": session_id,
                },
                "output": {
                    "reward": safe_reward,
                    "done": obs.done,
                    "error": error,
                    "score_so_far": obs_dict.get("score_so_far"),
                    "budget_remaining": obs_dict.get("budget_remaining"),
                    "tick": obs_dict.get("tick"),
                    "alert_count": len(obs_dict.get("alerts", [])),
                },
            },
        )
        return StepResponse(
            observation=obs_dict,
            reward=safe_reward,
            done=obs.done,
            info={},
        )

    @app.get("/state", response_model=dict[str, Any], tags=["openenv"])
    async def state(session_id: str = "default") -> dict[str, Any]:
        """Return current environment state."""
        env = _get_session(session_id)
        return env.state.model_dump()

    # -----------------------------------------------------------------------
    # Homepage
    # -----------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def homepage() -> HTMLResponse:
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="en">
              <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="width=device-width, initial-scale=1" />
                <title>SRE Incident Response</title>
                <style>
                  :root {
                    color-scheme: light;
                    --bg: #f3efe7;
                    --panel: rgba(255, 252, 246, 0.9);
                    --text: #1f1c18;
                    --muted: #5f5a54;
                    --accent: #0c6a5b;
                    --border: #d7d0c1;
                  }
                  * { box-sizing: border-box; }
                  body {
                    margin: 0;
                    font-family: Georgia, "Times New Roman", serif;
                    background: linear-gradient(180deg, #faf7f0 0%, var(--bg) 100%);
                    color: var(--text);
                  }
                  main {
                    max-width: 860px;
                    margin: 0 auto;
                    padding: 60px 24px 80px;
                  }
                  h1 { font-size: 2.4rem; margin-bottom: 0.4em; color: var(--accent); }
                  p { font-size: 1.1rem; line-height: 1.7; color: var(--muted); }
                  .links { display: flex; gap: 16px; margin-top: 32px; flex-wrap: wrap; }
                  .btn {
                    display: inline-block;
                    padding: 10px 22px;
                    border-radius: 8px;
                    background: var(--accent);
                    color: #fff;
                    text-decoration: none;
                    font-family: -apple-system, sans-serif;
                    font-weight: 600;
                    font-size: 0.9rem;
                  }
                  .btn.outline {
                    background: transparent;
                    border: 1.5px solid var(--border);
                    color: var(--text);
                  }
                </style>
              </head>
              <body>
                <main>
                  <h1>🚨 SRE Incident Response</h1>
                  <p>
                    An OpenEnv benchmark where AI agents diagnose and remediate production incidents
                    in a causal microservice simulator. Five scenarios, easy → hard.
                    Obvious actions often make things worse — agents must investigate before acting.
                  </p>
                  <div class="links">
                    <a class="btn" href="/docs">API Docs (Swagger)</a>
                    <a class="btn outline" href="/health">Health Check</a>
                  </div>
                </main>
              </body>
            </html>
            """
        )

    return app


app = build_app()


def main(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
