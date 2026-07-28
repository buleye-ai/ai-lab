from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    allowed_namespaces: frozenset[str]
    prometheus_url: str
    loki_url: str
    query_timeout_seconds: float
    log_lookback_seconds: int
    log_limit: int

    @classmethod
    def from_env(cls) -> "Config":
        namespaces = os.getenv("ALLOWED_NAMESPACES", "demo,observability").split(",")
        return cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8080")),
            allowed_namespaces=frozenset(
                namespace.strip() for namespace in namespaces if namespace.strip()
            ),
            prometheus_url=os.getenv(
                "PROMETHEUS_URL",
                "http://monitoring-kube-prometheus-prometheus.observability.svc.cluster.local:9090",
            ).rstrip("/"),
            loki_url=os.getenv(
                "LOKI_URL",
                "http://loki-gateway.observability.svc.cluster.local",
            ).rstrip("/"),
            query_timeout_seconds=float(os.getenv("QUERY_TIMEOUT_SECONDS", "5")),
            log_lookback_seconds=int(os.getenv("LOG_LOOKBACK_SECONDS", "600")),
            log_limit=min(int(os.getenv("LOG_LIMIT", "50")), 200),
        )

