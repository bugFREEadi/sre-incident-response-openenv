from __future__ import annotations

from fastapi import Depends, Query
from openenv_core.env_server.http_server import create_app
from starlette.responses import HTMLResponse

from server.ops_auth import make_auth_dependency
from server.ops_models import (
    ActorIdentity,
    AdvisoryPreviewRequest,
    AdvisoryPreviewResponse,
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    ApprovalRecord,
    AuditEvent,
    BackupBundle,
    ControlPlaneStatus,
    DrillRunRequest,
    DrillRunResult,
    ExecutionRecord,
    ExecutionRequest,
    ExecutionResponse,
    ModeChangeRequest,
    ReadOnlyTelemetryResponse,
)
from server.ops_service import OpsControlPlaneService
from server.sre_incident_environment import SREIncidentEnvironment
from sre_incident_env.models import SREIncidentAction, SREIncidentObservation


def build_app(control_plane: OpsControlPlaneService | None = None):
    app = create_app(
        SREIncidentEnvironment,
        SREIncidentAction,
        SREIncidentObservation,
        env_name="sre_incident_env",
        max_concurrent_envs=8,
    )
    app.state.control_plane = control_plane or OpsControlPlaneService()

    viewer = make_auth_dependency(app.state.control_plane, {"viewer", "agent", "operator", "approver", "admin"})
    operator = make_auth_dependency(app.state.control_plane, {"operator", "approver", "admin"})
    approver = make_auth_dependency(app.state.control_plane, {"approver", "admin"})
    admin = make_auth_dependency(app.state.control_plane, {"admin"})

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
                    --panel-strong: #fffdf8;
                    --text: #1f1c18;
                    --muted: #5f5a54;
                    --accent: #0c6a5b;
                    --accent-2: #9a3d2a;
                    --border: #d7d0c1;
                    --shadow: 0 18px 48px rgba(44, 35, 24, 0.08);
                  }

                  * { box-sizing: border-box; }

                  body {
                    margin: 0;
                    font-family: Georgia, "Times New Roman", serif;
                    background:
                      radial-gradient(circle at top left, rgba(12, 106, 91, 0.12), transparent 28%),
                      radial-gradient(circle at top right, rgba(154, 61, 42, 0.08), transparent 24%),
                      linear-gradient(180deg, #faf7f0 0%, var(--bg) 100%);
                    color: var(--text);
                  }

                  main {
                    max-width: 1160px;
                    margin: 0 auto;
                    padding: 44px 20px 88px;
                  }

                  .eyebrow {
                    display: inline-flex;
                    align-items: center;
                    gap: 8px;
                    padding: 7px 12px;
                    border-radius: 999px;
                    border: 1px solid var(--border);
                    background: rgba(255, 255, 255, 0.72);
                    font: 600 12px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                    letter-spacing: 0.08em;
                    text-transform: uppercase;
                    color: var(--accent);
                  }

                  .hero {
                    display: grid;
                    grid-template-columns: minmax(0, 1.4fr) minmax(300px, 0.9fr);
                    gap: 28px;
                    align-items: start;
                    margin-top: 22px;
                  }

                  .hero-copy h1 {
                    margin: 0 0 14px;
                    font-size: clamp(3rem, 6vw, 5.4rem);
                    line-height: 0.94;
                    letter-spacing: -0.05em;
                  }

                  .hero-copy p {
                    margin: 0 0 18px;
                    max-width: 760px;
                    font-size: 1.12rem;
                    line-height: 1.72;
                    color: var(--muted);
                  }

                  .cta-row {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 12px;
                    margin-top: 24px;
                  }

                  .btn, .subtle-link {
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    text-decoration: none;
                    border-radius: 999px;
                    padding: 12px 18px;
                    font: 600 0.95rem/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                    transition: transform 0.18s ease, border-color 0.18s ease;
                  }

                  .btn:hover, .subtle-link:hover, .card:hover, .mini-card:hover {
                    transform: translateY(-2px);
                  }

                  .btn-primary {
                    background: var(--accent);
                    color: #fff;
                    border: 1px solid var(--accent);
                  }

                  .btn-secondary {
                    background: transparent;
                    color: var(--text);
                    border: 1px solid var(--border);
                  }

                  .hero-panel, .section-panel {
                    padding: 24px;
                    border-radius: 28px;
                    border: 1px solid var(--border);
                    background: var(--panel);
                    box-shadow: var(--shadow);
                    backdrop-filter: blur(8px);
                  }

                  .hero-panel h2, .section-panel h2 {
                    margin: 0 0 10px;
                    font-size: 1.35rem;
                    line-height: 1.15;
                  }

                  .mono, code {
                    font: 500 0.95rem/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                  }

                  code {
                    background: #efe8dc;
                    border-radius: 8px;
                    padding: 0.12rem 0.35rem;
                  }

                  .mini-grid, .card-grid, .three-grid {
                    display: grid;
                    gap: 16px;
                  }

                  .mini-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    margin-top: 18px;
                  }

                  .mini-card, .card {
                    display: block;
                    padding: 18px;
                    border-radius: 20px;
                    border: 1px solid var(--border);
                    background: var(--panel-strong);
                    color: inherit;
                    text-decoration: none;
                    transition: transform 0.18s ease, border-color 0.18s ease;
                  }

                  .mini-card strong, .card strong {
                    display: block;
                    margin-bottom: 8px;
                    font: 600 1rem/1.25 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                  }

                  .mini-card span, .card span, li, .kicker, .metric-value, .metric-label {
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                  }

                  .section {
                    margin-top: 22px;
                  }

                  .section-header {
                    display: flex;
                    justify-content: space-between;
                    gap: 14px;
                    align-items: end;
                    margin-bottom: 14px;
                  }

                  .section-header p {
                    margin: 0;
                    max-width: 760px;
                    color: var(--muted);
                    font-size: 1rem;
                    line-height: 1.6;
                  }

                  .card-grid {
                    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
                  }

                  .three-grid {
                    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                  }

                  .metric-strip {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
                    gap: 14px;
                    margin-top: 18px;
                  }

                  .metric {
                    padding: 16px;
                    border-radius: 18px;
                    border: 1px solid var(--border);
                    background: rgba(255, 255, 255, 0.6);
                  }

                  .metric-value {
                    display: block;
                    font-size: 1.5rem;
                    font-weight: 700;
                    color: var(--text);
                  }

                  .metric-label {
                    display: block;
                    margin-top: 4px;
                    color: var(--muted);
                    font-size: 0.92rem;
                  }

                  .endpoint-list, .checklist {
                    margin: 14px 0 0;
                    padding-left: 18px;
                    color: var(--muted);
                  }

                  .endpoint-list li + li, .checklist li + li {
                    margin-top: 8px;
                  }

                  .endpoint-list strong {
                    color: var(--text);
                  }

                  .kicker {
                    display: inline-block;
                    margin-bottom: 8px;
                    color: var(--accent-2);
                    font-size: 0.78rem;
                    font-weight: 700;
                    letter-spacing: 0.08em;
                    text-transform: uppercase;
                  }

                  .footer-note {
                    margin-top: 28px;
                    color: var(--muted);
                    font: 500 0.95rem/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                  }

                  @media (max-width: 900px) {
                    .hero {
                      grid-template-columns: 1fr;
                    }

                    .mini-grid {
                      grid-template-columns: 1fr;
                    }
                  }
                </style>
              </head>
              <body>
                <main>
                  <div class="eyebrow">OpenEnv Benchmark + Production Safety Shell</div>

                  <section class="hero">
                    <div class="hero-copy">
                      <h1>SRE Incident Response</h1>
                      <p>
                        A causal incident benchmark for training and evaluating incident agents, wrapped in a
                        production-minded control plane for advisory previews, approval gates, audit trails,
                        tenant isolation, and drill-gated automation.
                      </p>
                      <p>
                        Use <code>/reset</code>, <code>/step</code>, and <code>/state</code> for the deterministic
                        simulator. Use <code>/ops/v1/*</code> for real telemetry adapters, approval workflows,
                        execution tracking, and operational guardrails.
                      </p>
                      <div class="cta-row">
                        <a class="btn btn-primary" href="/docs">Open API Docs</a>
                        <a class="btn btn-secondary" href="/metadata">View Metadata</a>
                        <a class="btn btn-secondary" href="/openapi.json">Download OpenAPI</a>
                      </div>

                      <div class="metric-strip">
                        <div class="metric">
                          <span class="metric-value">5</span>
                          <span class="metric-label">Production-style incident scenarios</span>
                        </div>
                        <div class="metric">
                          <span class="metric-value">2-layer</span>
                          <span class="metric-label">Recovery + decision-quality grading</span>
                        </div>
                        <div class="metric">
                          <span class="metric-value">3</span>
                          <span class="metric-label">Execution modes from advisory to gated automation</span>
                        </div>
                        <div class="metric">
                          <span class="metric-value">/ops/v1</span>
                          <span class="metric-label">Operational control-plane surface</span>
                        </div>
                      </div>
                    </div>

                    <aside class="hero-panel">
                      <span class="kicker">At A Glance</span>
                      <h2>What Lives Here</h2>
                      <p>
                        This service is both a benchmark and a safety harness. It is useful for offline agent
                        evaluation, internal incident drills, and human-in-the-loop production pilots.
                      </p>
                      <div class="mini-grid">
                        <a class="mini-card" href="/health">
                          <strong>Health</strong>
                          <span>Runtime readiness and validator heartbeat.</span>
                        </a>
                        <a class="mini-card" href="/schema">
                          <strong>Schema</strong>
                          <span>Typed action, observation, state, and control-plane contracts.</span>
                        </a>
                        <a class="mini-card" href="/metadata">
                          <strong>Metadata</strong>
                          <span>Benchmark identity, description, and versioning.</span>
                        </a>
                        <a class="mini-card" href="/docs">
                          <strong>Interactive Docs</strong>
                          <span>Explore simulator and ops endpoints in one place.</span>
                        </a>
                      </div>
                    </aside>
                  </section>

                  <section class="section">
                    <div class="section-header">
                      <div>
                        <span class="kicker">Core Surfaces</span>
                        <h2>Two APIs, One Runtime</h2>
                      </div>
                      <p>
                        The simulator stays deterministic and benchmark-friendly, while the ops shell adds
                        safer real-world adoption patterns around it.
                      </p>
                    </div>
                    <div class="card-grid">
                      <div class="card">
                        <strong>OpenEnv Simulator</strong>
                        <span>Deterministic incident episodes with typed actions and partial observations.</span>
                        <ul class="endpoint-list">
                          <li><strong>POST</strong> <code>/reset</code></li>
                          <li><strong>POST</strong> <code>/step</code></li>
                          <li><strong>GET</strong> <code>/state</code></li>
                        </ul>
                      </div>
                      <div class="card">
                        <strong>Read-Only Telemetry</strong>
                        <span>Pluggable adapters for logs, metrics, deploy history, and topology.</span>
                        <ul class="endpoint-list">
                          <li><strong>GET</strong> <code>/ops/v1/logs</code></li>
                          <li><strong>GET</strong> <code>/ops/v1/metrics</code></li>
                          <li><strong>GET</strong> <code>/ops/v1/deploy-history</code></li>
                          <li><strong>GET</strong> <code>/ops/v1/topology</code></li>
                        </ul>
                      </div>
                      <div class="card">
                        <strong>Control Plane</strong>
                        <span>Advisories, approvals, executions, backups, drills, and mode changes.</span>
                        <ul class="endpoint-list">
                          <li><strong>POST</strong> <code>/ops/v1/advisories/preview</code></li>
                          <li><strong>POST</strong> <code>/ops/v1/approvals</code></li>
                          <li><strong>POST</strong> <code>/ops/v1/actions/execute</code></li>
                          <li><strong>POST</strong> <code>/ops/v1/drills/run</code></li>
                        </ul>
                      </div>
                    </div>
                  </section>

                  <section class="section section-panel">
                    <div class="section-header">
                      <div>
                        <span class="kicker">Operational Model</span>
                        <h2>How Teams Use It</h2>
                      </div>
                      <p>
                        The intended rollout is cautious by design: benchmark first, advisory mode second,
                        execution only after drills, approvals, and policy checks pass.
                      </p>
                    </div>
                    <div class="three-grid">
                      <div class="card">
                        <strong>1. Benchmark</strong>
                        <span>Test an incident agent against tricky fault-domain scenarios before it sees prod telemetry.</span>
                      </div>
                      <div class="card">
                        <strong>2. Advisory Mode</strong>
                        <span>Connect real telemetry adapters and let the agent suggest actions without mutation rights.</span>
                      </div>
                      <div class="card">
                        <strong>3. Approval-Gated Execution</strong>
                        <span>Require approvals, allowlists, policies, tenant isolation, and recent passing drills.</span>
                      </div>
                    </div>
                    <ul class="checklist">
                      <li>Tenant-scoped approvals, audit records, drill history, and execution records.</li>
                      <li>Support for sqlite by default and Postgres when distributed persistence is needed.</li>
                      <li>Policy rules for service, tenant, role, action type, UTC time windows, replica caps, and rate limits.</li>
                      <li>Webhook-based remediation with operation IDs and optional verification polling.</li>
                    </ul>
                  </section>

                  <p class="footer-note">
                    Quick links:
                    <a href="/docs">API Docs</a>,
                    <a href="/metadata">metadata</a>,
                    <a href="/schema">schema</a>,
                    <a href="/health">health</a>.
                    For repo-level guidance, see the README and production guide shipped with this project.
                  </p>
                </main>
              </body>
            </html>
            """
        )

    @app.get("/ops/v1/status", response_model=ControlPlaneStatus, tags=["ops"])
    async def ops_status(actor: ActorIdentity = Depends(viewer)) -> ControlPlaneStatus:
        return app.state.control_plane.status(actor)

    @app.get("/ops/v1/logs", response_model=ReadOnlyTelemetryResponse, tags=["ops"])
    async def ops_logs(
        service: str = Query(...),
        tail_n: int = Query(20, ge=1, le=500),
        actor: ActorIdentity = Depends(viewer),
    ) -> ReadOnlyTelemetryResponse:
        return await app.state.control_plane.fetch_logs(actor, service, tail_n)

    @app.get("/ops/v1/metrics", response_model=ReadOnlyTelemetryResponse, tags=["ops"])
    async def ops_metrics(
        service: str = Query(...),
        lookback_minutes: int = Query(15, ge=1, le=1440),
        actor: ActorIdentity = Depends(viewer),
    ) -> ReadOnlyTelemetryResponse:
        return await app.state.control_plane.fetch_metrics(actor, service, lookback_minutes)

    @app.get("/ops/v1/deploy-history", response_model=ReadOnlyTelemetryResponse, tags=["ops"])
    async def ops_deploy_history(
        service: str = Query(...),
        limit: int = Query(20, ge=1, le=200),
        actor: ActorIdentity = Depends(viewer),
    ) -> ReadOnlyTelemetryResponse:
        return await app.state.control_plane.fetch_deploy_history(actor, service, limit)

    @app.get("/ops/v1/topology", response_model=ReadOnlyTelemetryResponse, tags=["ops"])
    async def ops_topology(
        service: str | None = Query(default=None),
        actor: ActorIdentity = Depends(viewer),
    ) -> ReadOnlyTelemetryResponse:
        return await app.state.control_plane.fetch_topology(actor, service)

    @app.post("/ops/v1/advisories/preview", response_model=AdvisoryPreviewResponse, tags=["ops"])
    async def advisory_preview(
        request: AdvisoryPreviewRequest,
        actor: ActorIdentity = Depends(operator),
    ) -> AdvisoryPreviewResponse:
        return app.state.control_plane.preview_action(actor, request)

    @app.post("/ops/v1/approvals", response_model=ApprovalRecord, tags=["ops"])
    async def request_approval(
        request: ApprovalCreateRequest,
        actor: ActorIdentity = Depends(operator),
    ) -> ApprovalRecord:
        return app.state.control_plane.create_approval(actor, request)

    @app.get("/ops/v1/approvals/{approval_id}", response_model=ApprovalRecord, tags=["ops"])
    async def get_approval(
        approval_id: str,
        actor: ActorIdentity = Depends(operator),
    ) -> ApprovalRecord:
        return app.state.control_plane.get_approval(actor, approval_id)

    @app.post("/ops/v1/approvals/{approval_id}/approve", response_model=ApprovalRecord, tags=["ops"])
    async def approve(
        approval_id: str,
        request: ApprovalDecisionRequest,
        actor: ActorIdentity = Depends(approver),
    ) -> ApprovalRecord:
        return app.state.control_plane.approve(actor, approval_id, request)

    @app.post("/ops/v1/approvals/{approval_id}/reject", response_model=ApprovalRecord, tags=["ops"])
    async def reject(
        approval_id: str,
        request: ApprovalDecisionRequest,
        actor: ActorIdentity = Depends(approver),
    ) -> ApprovalRecord:
        return app.state.control_plane.reject(actor, approval_id, request)

    @app.post("/ops/v1/actions/execute", response_model=ExecutionResponse, tags=["ops"])
    async def execute_action(
        request: ExecutionRequest,
        actor: ActorIdentity = Depends(operator),
    ) -> ExecutionResponse:
        return await app.state.control_plane.execute_action(actor, request)

    @app.get("/ops/v1/executions/{execution_id}", response_model=ExecutionRecord, tags=["ops"])
    async def get_execution(
        execution_id: str,
        actor: ActorIdentity = Depends(operator),
    ) -> ExecutionRecord:
        return app.state.control_plane.get_execution(actor, execution_id)

    @app.get("/ops/v1/audit", response_model=list[AuditEvent], tags=["ops"])
    async def audit_log(
        limit: int = Query(100, ge=1, le=500),
        actor: ActorIdentity = Depends(approver),
    ) -> list[AuditEvent]:
        return app.state.control_plane.list_audit(actor, limit)

    @app.post("/ops/v1/drills/run", response_model=DrillRunResult, tags=["ops"])
    async def run_drills(
        request: DrillRunRequest,
        actor: ActorIdentity = Depends(admin),
    ) -> DrillRunResult:
        return app.state.control_plane.run_drills(actor, request)

    @app.get("/ops/v1/drills/latest", response_model=DrillRunResult | None, tags=["ops"])
    async def latest_drill(actor: ActorIdentity = Depends(admin)) -> DrillRunResult | None:
        return app.state.control_plane.latest_drill(actor)

    @app.post("/ops/v1/mode", response_model=ControlPlaneStatus, tags=["ops"])
    async def set_mode(
        request: ModeChangeRequest,
        actor: ActorIdentity = Depends(admin),
    ) -> ControlPlaneStatus:
        return app.state.control_plane.set_execution_mode(actor, request)

    @app.get("/ops/v1/admin/backup", response_model=BackupBundle, tags=["ops"])
    async def backup_export(actor: ActorIdentity = Depends(admin)) -> BackupBundle:
        return app.state.control_plane.export_backup(actor)

    return app


app = build_app()


def main(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
