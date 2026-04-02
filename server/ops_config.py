from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from server.ops_models import ActorIdentity, ExecutionMode, PolicyRule


@dataclass
class OpsControlPlaneConfig:
    execution_mode: ExecutionMode = "advisory_only"
    require_auth: bool = True
    disable_auth_for_local_dev: bool = False
    approval_required_for_mutations: bool = True
    drill_gate_enabled: bool = True
    drill_validity_hours: int = 24
    minimum_drill_average_score: float = 0.75
    minimum_scenario_score: float = 0.65
    approval_ttl_minutes: int = 60
    max_scale_replicas: int = 20
    max_rate_limit_rps: int = 10000
    admin_rate_limit_count: int = 20
    admin_rate_limit_window_seconds: int = 60
    database_path: str = "data/ops_control_plane.sqlite3"
    database_url: str | None = None
    audit_jsonl_path: str | None = "data/ops_audit.jsonl"
    allowed_services: set[str] = field(default_factory=set)
    allowed_mutating_actions: set[str] = field(default_factory=set)
    api_tokens: dict[str, ActorIdentity] = field(default_factory=dict)
    policy_rules: list[PolicyRule] = field(default_factory=list)
    prometheus_base_url: str | None = None
    prometheus_bearer_token: str | None = None
    prometheus_query_template: str = 'up{job="{service}"}'
    loki_base_url: str | None = None
    loki_bearer_token: str | None = None
    loki_query_template: str = '{service="{service}"}'
    argocd_base_url: str | None = None
    argocd_bearer_token: str | None = None
    topology_file: str | None = None
    topology_url: str | None = None
    topology_bearer_token: str | None = None
    remediation_webhook_url: str | None = None
    remediation_bearer_token: str | None = None
    remediation_status_url_template: str | None = None
    remediation_verify_attempts: int = 5
    remediation_verify_delay_seconds: float = 1.0

    @property
    def auth_configured(self) -> bool:
        return bool(self.api_tokens)


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def _parse_tokens(raw: str | None) -> dict[str, ActorIdentity]:
    if not raw:
        return {}
    payload = json.loads(raw)
    tokens: dict[str, ActorIdentity] = {}
    for token, actor_payload in payload.items():
        tokens[token] = ActorIdentity.model_validate(actor_payload)
    return tokens


def _resolve_secret(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("file://"):
        path = value.removeprefix("file://")
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    if value.startswith("env://"):
        return os.getenv(value.removeprefix("env://"))
    return value


def _parse_policy_rules(raw: str | None) -> list[PolicyRule]:
    if not raw:
        return []
    payload = json.loads(raw)
    return [PolicyRule.model_validate(item) for item in payload]


def load_ops_config() -> OpsControlPlaneConfig:
    return OpsControlPlaneConfig(
        execution_mode=os.getenv("OPS_EXECUTION_MODE", "advisory_only"),
        require_auth=_parse_bool(os.getenv("OPS_REQUIRE_AUTH"), True),
        disable_auth_for_local_dev=_parse_bool(os.getenv("OPS_DISABLE_AUTH_FOR_LOCAL_DEV"), False),
        approval_required_for_mutations=_parse_bool(
            os.getenv("OPS_APPROVAL_REQUIRED_FOR_MUTATIONS"),
            True,
        ),
        drill_gate_enabled=_parse_bool(os.getenv("OPS_DRILL_GATE_ENABLED"), True),
        drill_validity_hours=int(os.getenv("OPS_DRILL_VALIDITY_HOURS", "24")),
        minimum_drill_average_score=float(os.getenv("OPS_MINIMUM_DRILL_AVERAGE_SCORE", "0.75")),
        minimum_scenario_score=float(os.getenv("OPS_MINIMUM_SCENARIO_SCORE", "0.65")),
        approval_ttl_minutes=int(os.getenv("OPS_APPROVAL_TTL_MINUTES", "60")),
        max_scale_replicas=int(os.getenv("OPS_MAX_SCALE_REPLICAS", "20")),
        max_rate_limit_rps=int(os.getenv("OPS_MAX_RATE_LIMIT_RPS", "10000")),
        admin_rate_limit_count=int(os.getenv("OPS_ADMIN_RATE_LIMIT_COUNT", "20")),
        admin_rate_limit_window_seconds=int(os.getenv("OPS_ADMIN_RATE_LIMIT_WINDOW_SECONDS", "60")),
        database_path=os.getenv("OPS_DATABASE_PATH", "data/ops_control_plane.sqlite3"),
        database_url=_resolve_secret(os.getenv("OPS_DATABASE_URL")),
        audit_jsonl_path=os.getenv("OPS_AUDIT_JSONL_PATH", "data/ops_audit.jsonl"),
        allowed_services=_parse_csv_set(os.getenv("OPS_ALLOWED_SERVICES")),
        allowed_mutating_actions=_parse_csv_set(os.getenv("OPS_ALLOWED_MUTATING_ACTIONS")),
        api_tokens=_parse_tokens(os.getenv("OPS_API_TOKENS_JSON")),
        policy_rules=_parse_policy_rules(os.getenv("OPS_POLICY_RULES_JSON")),
        prometheus_base_url=os.getenv("OPS_PROMETHEUS_BASE_URL"),
        prometheus_bearer_token=_resolve_secret(os.getenv("OPS_PROMETHEUS_BEARER_TOKEN")),
        prometheus_query_template=os.getenv(
            "OPS_PROMETHEUS_QUERY_TEMPLATE",
            'up{job="{service}"}',
        ),
        loki_base_url=os.getenv("OPS_LOKI_BASE_URL"),
        loki_bearer_token=_resolve_secret(os.getenv("OPS_LOKI_BEARER_TOKEN")),
        loki_query_template=os.getenv(
            "OPS_LOKI_QUERY_TEMPLATE",
            '{service="{service}"}',
        ),
        argocd_base_url=os.getenv("OPS_ARGOCD_BASE_URL"),
        argocd_bearer_token=_resolve_secret(os.getenv("OPS_ARGOCD_BEARER_TOKEN")),
        topology_file=os.getenv("OPS_TOPOLOGY_FILE"),
        topology_url=os.getenv("OPS_TOPOLOGY_URL"),
        topology_bearer_token=_resolve_secret(os.getenv("OPS_TOPOLOGY_BEARER_TOKEN")),
        remediation_webhook_url=os.getenv("OPS_REMEDIATION_WEBHOOK_URL"),
        remediation_bearer_token=_resolve_secret(os.getenv("OPS_REMEDIATION_BEARER_TOKEN")),
        remediation_status_url_template=os.getenv("OPS_REMEDIATION_STATUS_URL_TEMPLATE"),
        remediation_verify_attempts=int(os.getenv("OPS_REMEDIATION_VERIFY_ATTEMPTS", "5")),
        remediation_verify_delay_seconds=float(os.getenv("OPS_REMEDIATION_VERIFY_DELAY_SECONDS", "1.0")),
    )
