from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

import httpx

from server.ops_models import (
    DeployRecord,
    LogRecord,
    MetricPoint,
    MetricSeries,
    TopologyRecord,
    utcnow_iso,
)


class LogsAdapter(Protocol):
    backend_name: str

    async def fetch_logs(self, service: str, tail_n: int = 20) -> list[LogRecord]: ...


class MetricsAdapter(Protocol):
    backend_name: str

    async def fetch_metrics(self, service: str, lookback_minutes: int = 15) -> list[MetricSeries]: ...


class DeployHistoryAdapter(Protocol):
    backend_name: str

    async def fetch_deploy_history(self, service: str, limit: int = 20) -> list[DeployRecord]: ...


class TopologyAdapter(Protocol):
    backend_name: str

    async def fetch_topology(self, service: str | None = None) -> list[TopologyRecord]: ...


class RemediationAdapter(Protocol):
    backend_name: str

    async def execute(self, payload: dict[str, Any], dry_run: bool = False) -> dict[str, Any]: ...

    async def verify(self, operation_id: str) -> dict[str, Any]: ...


class LokiLogsAdapter:
    backend_name = "loki"

    def __init__(self, base_url: str, query_template: str, bearer_token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.query_template = query_template
        self.bearer_token = bearer_token

    async def fetch_logs(self, service: str, tail_n: int = 20) -> list[LogRecord]:
        headers = _bearer_headers(self.bearer_token)
        params = {
            "query": self.query_template.format(service=service),
            "limit": tail_n,
            "direction": "BACKWARD",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self.base_url}/loki/api/v1/query",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()

        streams = payload.get("data", {}).get("result", [])
        records: list[LogRecord] = []
        for stream in streams:
            labels = stream.get("stream", {})
            for timestamp, message in stream.get("values", []):
                records.append(
                    LogRecord(
                        timestamp=timestamp,
                        service=service,
                        level=labels.get("level", "INFO"),
                        message=message,
                        labels=labels,
                        source=self.backend_name,
                    )
                )
        return records[:tail_n]


class PrometheusMetricsAdapter:
    backend_name = "prometheus"

    def __init__(self, base_url: str, query_template: str, bearer_token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.query_template = query_template
        self.bearer_token = bearer_token

    async def fetch_metrics(self, service: str, lookback_minutes: int = 15) -> list[MetricSeries]:
        headers = _bearer_headers(self.bearer_token)
        params = {
            "query": self.query_template.format(service=service),
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/query",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
        results = payload.get("data", {}).get("result", [])
        series: list[MetricSeries] = []
        for item in results:
            metric = item.get("metric", {})
            value = item.get("value")
            points: list[MetricPoint] = []
            if isinstance(value, list) and len(value) == 2:
                points.append(
                    MetricPoint(
                        timestamp=str(value[0]),
                        value=float(value[1]),
                    )
                )
            series.append(
                MetricSeries(
                    name=metric.get("__name__", "prometheus_query"),
                    service=service,
                    points=points,
                    labels={key: str(raw) for key, raw in metric.items()},
                )
            )
        return series


class ArgoCDDeployHistoryAdapter:
    backend_name = "argocd"

    def __init__(self, base_url: str, bearer_token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token

    async def fetch_deploy_history(self, service: str, limit: int = 20) -> list[DeployRecord]:
        headers = _bearer_headers(self.bearer_token)
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/applications/{service}",
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()

        history = payload.get("status", {}).get("history", [])
        records: list[DeployRecord] = []
        previous_revision: str | None = None
        for item in history[:limit]:
            revision = item.get("revision")
            records.append(
                DeployRecord(
                    service=service,
                    version_from=previous_revision,
                    version_to=revision,
                    timestamp=item.get("deployedAt"),
                    triggered_by=item.get("initiatedBy", {}).get("username"),
                    source=self.backend_name,
                    metadata={
                        "id": item.get("id"),
                        "deployStartedAt": item.get("deployStartedAt"),
                    },
                )
            )
            previous_revision = revision
        return records


class StaticTopologyAdapter:
    backend_name = "static_topology"

    def __init__(self, file_path: str) -> None:
        self.file_path = Path(file_path)

    async def fetch_topology(self, service: str | None = None) -> list[TopologyRecord]:
        payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        services = payload.get("services", payload)
        if isinstance(services, dict):
            items = [
                TopologyRecord(service=name, **details)
                for name, details in services.items()
                if isinstance(details, dict)
            ]
        else:
            items = [TopologyRecord.model_validate(item) for item in services]
        if service:
            items = [item for item in items if item.service == service]
        return items


class HttpTopologyAdapter:
    backend_name = "http_topology"

    def __init__(self, base_url: str, bearer_token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token

    async def fetch_topology(self, service: str | None = None) -> list[TopologyRecord]:
        headers = _bearer_headers(self.bearer_token)
        url = self.base_url
        if service:
            url = f"{url.rstrip('/')}/{service}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
        items = payload.get("services", payload)
        if isinstance(items, dict):
            return [
                TopologyRecord(service=name, **details)
                for name, details in items.items()
                if isinstance(details, dict)
            ]
        if isinstance(items, list):
            return [TopologyRecord.model_validate(item) for item in items]
        return [
            TopologyRecord(
                service=service or "unknown",
                metadata={"raw": items, "fetched_at": utcnow_iso()},
            )
        ]


class WebhookRemediationAdapter:
    backend_name = "webhook"

    def __init__(
        self,
        url: str,
        bearer_token: str | None = None,
        status_url_template: str | None = None,
        verify_attempts: int = 5,
        verify_delay_seconds: float = 1.0,
    ) -> None:
        self.url = url
        self.bearer_token = bearer_token
        self.status_url_template = status_url_template
        self.verify_attempts = verify_attempts
        self.verify_delay_seconds = verify_delay_seconds

    async def execute(self, payload: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        headers = _bearer_headers(self.bearer_token)
        headers["Idempotency-Key"] = str(payload.get("approval_id") or payload.get("incident_id") or utcnow_iso())
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                self.url,
                headers=headers,
                json={**payload, "dry_run": dry_run},
            )
            response.raise_for_status()
            result = response.json()
        result.setdefault("operation_id", payload.get("approval_id"))
        if dry_run or not self.status_url_template:
            result.setdefault("status", "accepted")
            return result
        operation_id = str(result.get("operation_id"))
        verification = await self.verify(operation_id)
        result["verification"] = verification
        result["status"] = verification.get("status", result.get("status", "unknown"))
        return result

    async def verify(self, operation_id: str) -> dict[str, Any]:
        if not self.status_url_template:
            return {"status": "unknown", "operation_id": operation_id}
        headers = _bearer_headers(self.bearer_token)
        url = self.status_url_template.format(operation_id=operation_id)
        last_payload: dict[str, Any] = {"status": "unknown", "operation_id": operation_id}
        async with httpx.AsyncClient(timeout=20.0) as client:
            for attempt in range(1, self.verify_attempts + 1):
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                last_payload = response.json()
                last_payload.setdefault("operation_id", operation_id)
                last_payload.setdefault("attempt", attempt)
                if last_payload.get("status") in {"succeeded", "failed"}:
                    return last_payload
                if attempt < self.verify_attempts:
                    await asyncio.sleep(self.verify_delay_seconds)
        return last_payload


class NoOpRemediationAdapter:
    backend_name = "disabled"

    async def execute(self, payload: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        return {
            "accepted": False,
            "dry_run": dry_run,
            "message": "No remediation backend configured",
            "payload": payload,
            "operation_id": payload.get("approval_id"),
            "status": "failed",
        }

    async def verify(self, operation_id: str) -> dict[str, Any]:
        return {"operation_id": operation_id, "status": "failed"}


def _bearer_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}
