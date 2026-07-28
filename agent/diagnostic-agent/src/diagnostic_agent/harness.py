from __future__ import annotations

import re
import time
import uuid
from typing import Any, Protocol

from .config import Config
from .tools import ToolResult


K8S_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")


class Tools(Protocol):
    def kubernetes_get_pod(self, namespace: str, pod: str) -> ToolResult: ...

    def prometheus_restarts(
        self,
        namespace: str,
        pod: str,
        container: str | None,
    ) -> ToolResult: ...

    def loki_errors(self, namespace: str, pod: str) -> ToolResult: ...


class ValidationError(ValueError):
    pass


class DiagnosticHarness:
    def __init__(self, config: Config, tools: Tools):
        self.config = config
        self.tools = tools

    def handle_webhook(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        _validate_webhook(payload)
        return [
            self._diagnose(alert, payload["status"])
            for alert in payload["alerts"]
        ]

    def _diagnose(
        self,
        alert: dict[str, Any],
        event_status: str,
    ) -> dict[str, Any]:
        labels = alert["labels"]
        alert_name = labels.get("alertname", "UnknownAlert")
        fingerprint = alert.get("fingerprint", "")
        trace_id = f"diag-{uuid.uuid4().hex}"
        generated_at = _utc_now()
        evidence = [
            {
                "source": "alertmanager",
                "observation": f"{alert_name} 当前为 {event_status}。",
                "collected_at": generated_at,
                "query_ref": f"alert:{fingerprint or 'unknown'}",
            }
        ]
        tool_calls: list[dict[str, Any]] = []

        if event_status == "resolved":
            return _report(
                trace_id=trace_id,
                event_status=event_status,
                generated_at=generated_at,
                alert_name=alert_name,
                fingerprint=fingerprint,
                labels=labels,
                summary=f"{alert_name} 已恢复，不执行新的诊断查询。",
                evidence=evidence,
                tool_calls=tool_calls,
            )

        namespace = labels.get("namespace")
        pod = labels.get("pod")
        container = labels.get("container")
        location_error = self._validate_target(namespace, pod)
        if location_error:
            return _report(
                trace_id=trace_id,
                event_status=event_status,
                generated_at=generated_at,
                alert_name=alert_name,
                fingerprint=fingerprint,
                labels=labels,
                summary=location_error,
                evidence=evidence,
                tool_calls=tool_calls,
                recommended_actions=[
                    "检查告警规则是否提供 namespace 和 pod 标签。"
                ],
            )

        assert namespace is not None
        assert pod is not None
        results = [
            self.tools.kubernetes_get_pod(namespace, pod),
            self.tools.prometheus_restarts(namespace, pod, container),
            self.tools.loki_errors(namespace, pod),
        ]
        tool_calls.extend(result.trace() for result in results)
        evidence.extend(_evidence_from_results(results))
        hypotheses = _build_hypotheses(results, evidence)
        summary = _build_summary(alert_name, results, hypotheses)

        return _report(
            trace_id=trace_id,
            event_status=event_status,
            generated_at=generated_at,
            alert_name=alert_name,
            fingerprint=fingerprint,
            labels=labels,
            summary=summary,
            evidence=evidence,
            hypotheses=hypotheses,
            recommended_actions=[
                "根据证据检查 Deployment 的 command、args、volumeMounts 和探针。",
                "修复通过 Git 提交，由 Argo CD 同步并观察告警是否 resolved。",
            ],
            tool_calls=tool_calls,
        )

    def _validate_target(
        self,
        namespace: str | None,
        pod: str | None,
    ) -> str | None:
        if not namespace or not pod:
            return "告警缺少 namespace 或 pod 标签，无法安全定位资源。"
        if namespace not in self.config.allowed_namespaces:
            return f"namespace {namespace!r} 不在诊断允许列表中。"
        if not K8S_NAME.fullmatch(namespace) or not K8S_NAME.fullmatch(pod):
            return "告警中的 namespace 或 pod 名称不符合 Kubernetes 名称规则。"
        return None


def _validate_webhook(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object")
    if payload.get("status") not in {"firing", "resolved"}:
        raise ValidationError("status must be firing or resolved")
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        raise ValidationError("alerts must be a non-empty array")
    for alert in alerts:
        if not isinstance(alert, dict) or not isinstance(alert.get("labels"), dict):
            raise ValidationError("each alert must contain labels")


def _evidence_from_results(results: list[ToolResult]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for result in results:
        if result.status != "succeeded":
            continue
        if result.tool == "kubernetes.get":
            observation = _pod_observation(result.data)
            source = "kubernetes"
        elif result.tool == "prometheus.query":
            observation = _prometheus_observation(result.data)
            source = "prometheus"
        else:
            observation = _loki_observation(result.data)
            source = "loki"
        evidence.append(
            {
                "source": source,
                "observation": observation,
                "collected_at": result.started_at,
                "query_ref": result.tool,
            }
        )
    return evidence


def _pod_observation(data: dict[str, Any]) -> str:
    statuses = data.get("status", {}).get("containerStatuses", [])
    if not statuses:
        return "Pod 没有容器状态信息。"
    parts = []
    for status in statuses:
        state = status.get("state", {})
        waiting = state.get("waiting", {})
        reason = waiting.get("reason") or next(iter(state), "unknown")
        parts.append(
            f"{status.get('name', 'unknown')} restartCount="
            f"{status.get('restartCount', 0)} state={reason}"
        )
    return "；".join(parts) + "。"


def _prometheus_observation(data: dict[str, Any]) -> str:
    results = data.get("data", {}).get("result", [])
    if not results:
        return "Prometheus 查询没有返回重启数据。"
    value = results[0].get("value", [None, "unknown"])[1]
    return f"最近十分钟容器重启增量合计为 {value}。"


def _loki_observation(data: dict[str, Any]) -> str:
    streams = data.get("data", {}).get("result", [])
    lines = [
        value[1]
        for stream in streams
        for value in stream.get("values", [])
        if len(value) >= 2
    ]
    if not lines:
        return "最近十分钟未发现匹配的错误日志。"
    sample = " | ".join(lines[:3])
    return f"最近十分钟发现 {len(lines)} 条匹配日志；样例：{sample[:500]}"


def _build_hypotheses(
    results: list[ToolResult],
    evidence: list[dict[str, str]],
) -> list[dict[str, Any]]:
    observations = " ".join(item["observation"].lower() for item in evidence)
    hypotheses: list[dict[str, Any]] = []
    if "crashloopbackoff" in observations:
        hypotheses.append(
            {
                "cause": "容器启动后持续退出，需结合日志检查启动参数或配置。",
                "confidence": 0.75,
                "evidence_refs": list(range(1, len(evidence))),
            }
        )
    if "not found" in observations:
        hypotheses.append(
            {
                "cause": "容器可能引用了不存在的文件、配置或依赖。",
                "confidence": 0.82,
                "evidence_refs": [
                    index
                    for index, item in enumerate(evidence)
                    if "not found" in item["observation"].lower()
                ],
            }
        )
    if not hypotheses and any(result.status == "failed" for result in results):
        hypotheses.append(
            {
                "cause": "诊断数据不完整，当前无法形成可靠根因判断。",
                "confidence": 0.2,
                "evidence_refs": [0],
            }
        )
    return hypotheses


def _build_summary(
    alert_name: str,
    results: list[ToolResult],
    hypotheses: list[dict[str, Any]],
) -> str:
    failures = sum(result.status == "failed" for result in results)
    if hypotheses:
        return f"{alert_name} 已完成只读诊断：{hypotheses[0]['cause']}"
    if failures:
        return f"{alert_name} 的部分查询失败，证据不足，不能可靠判断根因。"
    return f"{alert_name} 已完成只读查询，但尚无足够证据形成根因假设。"


def _report(
    *,
    trace_id: str,
    event_status: str,
    generated_at: str,
    alert_name: str,
    fingerprint: str,
    labels: dict[str, str],
    summary: str,
    evidence: list[dict[str, str]],
    tool_calls: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]] | None = None,
    recommended_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "trace_id": trace_id,
        "event_status": event_status,
        "generated_at": generated_at,
        "alert": {
            "name": alert_name,
            "fingerprint": fingerprint,
            "labels": {
                key: value
                for key, value in labels.items()
                if isinstance(value, str)
            },
        },
        "summary": summary,
        "evidence": evidence,
        "hypotheses": hypotheses or [],
        "recommended_actions": recommended_actions or [],
        "tool_calls": tool_calls,
        "safety": {
            "mode": "read-only",
            "automatic_action_taken": False,
            "writes_allowed": False,
        },
    }


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

