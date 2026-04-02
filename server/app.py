from __future__ import annotations

from fastapi import FastAPI
from openenv.core.env_server.http_server import create_app
from starlette.responses import HTMLResponse

from sre_incident_env.models import SREIncidentAction, SREIncidentObservation
from server.sre_incident_environment import SREIncidentEnvironment


openenv_app = create_app(
    SREIncidentEnvironment,
    SREIncidentAction,
    SREIncidentObservation,
    env_name="sre_incident_env",
    max_concurrent_envs=8,
)

app = FastAPI(
    title="SRE Incident Response OpenEnv",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def homepage() -> HTMLResponse:
    return HTMLResponse(
        """
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>SRE Incident Response OpenEnv</title>
            <style>
              :root {
                color-scheme: light;
                --bg: #f4f1ea;
                --panel: #fffdf8;
                --text: #1d1d1b;
                --muted: #5d5a55;
                --accent: #0e6b5c;
                --border: #d9d1c3;
                --shadow: 0 18px 48px rgba(41, 36, 29, 0.08);
              }

              * { box-sizing: border-box; }

              body {
                margin: 0;
                min-height: 100vh;
                font-family: Georgia, "Times New Roman", serif;
                background:
                  radial-gradient(circle at top left, rgba(14, 107, 92, 0.12), transparent 28%),
                  linear-gradient(180deg, #f8f4ec 0%, var(--bg) 100%);
                color: var(--text);
              }

              main {
                max-width: 920px;
                margin: 0 auto;
                padding: 56px 24px 80px;
              }

              .eyebrow {
                display: inline-block;
                margin-bottom: 16px;
                padding: 6px 10px;
                border: 1px solid var(--border);
                border-radius: 999px;
                font: 600 12px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                color: var(--accent);
                background: rgba(255, 253, 248, 0.9);
              }

              h1 {
                margin: 0 0 14px;
                font-size: clamp(2.4rem, 5vw, 4.6rem);
                line-height: 0.96;
                letter-spacing: -0.04em;
              }

              .lede {
                max-width: 720px;
                margin: 0 0 30px;
                font-size: 1.1rem;
                line-height: 1.65;
                color: var(--muted);
              }

              .panel {
                padding: 24px;
                border: 1px solid var(--border);
                border-radius: 24px;
                background: rgba(255, 253, 248, 0.94);
                box-shadow: var(--shadow);
              }

              .grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 16px;
                margin-top: 26px;
              }

              .card {
                display: block;
                padding: 18px;
                border: 1px solid var(--border);
                border-radius: 18px;
                background: white;
                color: inherit;
                text-decoration: none;
                transition: transform 0.18s ease, border-color 0.18s ease;
              }

              .card:hover {
                transform: translateY(-2px);
                border-color: var(--accent);
              }

              .card strong {
                display: block;
                margin-bottom: 8px;
                font-size: 1rem;
              }

              .card span,
              .meta,
              code {
                font: 500 0.95rem/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
              }

              .meta {
                margin-top: 24px;
                color: var(--muted);
              }

              ul {
                margin: 18px 0 0;
                padding-left: 18px;
                color: var(--muted);
              }

              li + li {
                margin-top: 8px;
              }

              code {
                padding: 0.12rem 0.35rem;
                border-radius: 8px;
                background: #f2eee5;
              }
            </style>
          </head>
          <body>
            <main>
              <div class="eyebrow">OpenEnv Benchmark</div>
              <h1>SRE Incident Response</h1>
              <p class="lede">
                A production-style incident diagnosis environment where the locally obvious fix can
                worsen the global system state. Agents must inspect logs, metrics, and dependency
                signals before declaring root cause and applying a safe remediation.
              </p>

              <section class="panel">
                <div class="grid">
                  <a class="card" href="/health">
                    <strong>Health</strong>
                    <span>Lightweight readiness check for validators and operators.</span>
                  </a>
                  <a class="card" href="/metadata">
                    <strong>Metadata</strong>
                    <span>Name, description, version, and benchmark metadata.</span>
                  </a>
                  <a class="card" href="/schema">
                    <strong>Schema</strong>
                    <span>Typed action, observation, and state models.</span>
                  </a>
                  <a class="card" href="/openapi.json">
                    <strong>OpenAPI</strong>
                    <span>Machine-readable API contract for the environment.</span>
                  </a>
                </div>

                <p class="meta">
                  Core interaction happens over the OpenEnv endpoints <code>/reset</code>,
                  <code>/step</code>, and <code>/state</code>.
                </p>

                <ul>
                  <li>Tasks cover restart cascades, feature-flag corruption, bad rollbacks, cache stampedes, and retry storms.</li>
                  <li>Scoring combines recovery quality with decision quality so brute-force remediation scores poorly.</li>
                  <li>This Space is intended for agent evaluation and automated validation.</li>
                </ul>
              </section>
            </main>
          </body>
        </html>
        """
    )

app.mount("/", openenv_app)


def main(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
