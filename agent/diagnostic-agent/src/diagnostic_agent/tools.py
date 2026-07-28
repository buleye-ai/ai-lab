from __future__ import annotations

import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config


class ToolError(RuntimeError):
    pass


@dataclass
class ToolResult:
    tool: str
    status: str
    started_at: str
    duration_ms: int
    data: Any = None
    error: str | None = None

    def trace(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "tool": self.tool,
            "status": self.status,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
        }
        if self.error:
            result["error"] = self.error
        return result


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _request_json(
    url: str,
    timeout: float,
    headers: dict[str, str] | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", **(headers or {})},
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=ssl_context,
        ) as response:
            return json.load(response)
    except Exception as exc:
        raise ToolError(f"GET request failed: {type(exc).__name__}: {exc}") from exc


class ReadOnlyTools:
    def __init__(self, config: Config):
        self.config = config

    def kubernetes_get_pod(self, namespace: str, pod: str) -> ToolResult:
        started_at = _utc_now()
        started = time.monotonic()
        try:
            host = os.environ["KUBERNETES_SERVICE_HOST"]
            port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
            token = Path(
                "/var/run/secrets/kubernetes.io/serviceaccount/token"
            ).read_text().strip()
            ca_file = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
            path = (
                "/api/v1/namespaces/"
                f"{urllib.parse.quote(namespace, safe='')}/pods/"
                f"{urllib.parse.quote(pod, safe='')}"
            )
            data = _request_json(
                f"https://{host}:{port}{path}",
                self.config.query_timeout_seconds,
                headers={"Authorization": f"Bearer {token}"},
                ssl_context=ssl.create_default_context(cafile=ca_file),
            )
            return self._result("kubernetes.get", started_at, started, data=data)
        except Exception as exc:
            return self._result(
                "kubernetes.get",
                started_at,
                started,
                status="failed",
                error=str(exc),
            )

    def prometheus_restarts(
        self,
        namespace: str,
        pod: str,
        container: str | None,
    ) -> ToolResult:
        started_at = _utc_now()
        started = time.monotonic()
        labels = [
            f'namespace="{_promql_escape(namespace)}"',
            f'pod="{_promql_escape(pod)}"',
        ]
        if container:
            labels.append(f'container="{_promql_escape(container)}"')
        query = (
            "sum(increase(kube_pod_container_status_restarts_total"
            f"{{{','.join(labels)}}}[10m]))"
        )
        try:
            data = _request_json(
                f"{self.config.prometheus_url}/api/v1/query?"
                + urllib.parse.urlencode({"query": query}),
                self.config.query_timeout_seconds,
            )
            return self._result("prometheus.query", started_at, started, data=data)
        except Exception as exc:
            return self._result(
                "prometheus.query",
                started_at,
                started,
                status="failed",
                error=str(exc),
            )

    def loki_errors(self, namespace: str, pod: str) -> ToolResult:
        started_at = _utc_now()
        started = time.monotonic()
        end_ns = time.time_ns()
        start_ns = end_ns - self.config.log_lookback_seconds * 1_000_000_000
        query = (
            f'{{namespace="{_logql_escape(namespace)}",'
            f'pod="{_logql_escape(pod)}"}} '
            '|~ "(?i)error|fatal|panic|not found|refused|failed"'
        )
        params = urllib.parse.urlencode(
            {
                "query": query,
                "start": str(start_ns),
                "end": str(end_ns),
                "limit": str(self.config.log_limit),
                "direction": "backward",
            }
        )
        try:
            data = _request_json(
                f"{self.config.loki_url}/loki/api/v1/query_range?{params}",
                self.config.query_timeout_seconds,
            )
            return self._result("loki.query_range", started_at, started, data=data)
        except Exception as exc:
            return self._result(
                "loki.query_range",
                started_at,
                started,
                status="failed",
                error=str(exc),
            )

    @staticmethod
    def _result(
        tool: str,
        started_at: str,
        started: float,
        data: Any = None,
        status: str = "succeeded",
        error: str | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool=tool,
            status=status,
            started_at=started_at,
            duration_ms=max(0, round((time.monotonic() - started) * 1000)),
            data=data,
            error=error,
        )


def _promql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _logql_escape(value: str) -> str:
    return _promql_escape(value)

