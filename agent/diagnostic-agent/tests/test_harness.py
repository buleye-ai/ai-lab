from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diagnostic_agent.config import Config
from diagnostic_agent.harness import DiagnosticHarness, ValidationError
from diagnostic_agent.tools import ToolResult


class FakeTools:
    def kubernetes_get_pod(self, namespace: str, pod: str) -> ToolResult:
        return ToolResult(
            tool="kubernetes.get",
            status="succeeded",
            started_at="2026-07-28T13:31:01Z",
            duration_ms=1,
            data={
                "status": {
                    "containerStatuses": [
                        {
                            "name": "api",
                            "restartCount": 8,
                            "state": {
                                "waiting": {
                                    "reason": "CrashLoopBackOff"
                                }
                            },
                        }
                    ]
                }
            },
        )

    def prometheus_restarts(
        self,
        namespace: str,
        pod: str,
        container: str | None,
    ) -> ToolResult:
        return ToolResult(
            tool="prometheus.query",
            status="succeeded",
            started_at="2026-07-28T13:31:02Z",
            duration_ms=1,
            data={
                "data": {
                    "result": [
                        {
                            "value": [
                                0,
                                "8"
                            ]
                        }
                    ]
                }
            },
        )

    def loki_errors(self, namespace: str, pod: str) -> ToolResult:
        return ToolResult(
            tool="loki.query_range",
            status="succeeded",
            started_at="2026-07-28T13:31:03Z",
            duration_ms=1,
            data={
                "data": {
                    "result": [
                        {
                            "values": [
                                [
                                    "0",
                                    "configuration file not found"
                                ]
                            ]
                        }
                    ]
                }
            },
        )


def config() -> Config:
    return Config(
        host="127.0.0.1",
        port=8080,
        allowed_namespaces=frozenset({"demo"}),
        prometheus_url="http://prometheus",
        loki_url="http://loki",
        query_timeout_seconds=1,
        log_lookback_seconds=600,
        log_limit=50,
    )


class DiagnosticHarnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = DiagnosticHarness(config(), FakeTools())
        self.payload = json.loads(
            (ROOT / "examples" / "alertmanager-firing.json").read_text()
        )

    def test_firing_event_produces_evidence_and_hypothesis(self) -> None:
        report = self.harness.handle_webhook(self.payload)[0]

        self.assertEqual(report["event_status"], "firing")
        self.assertEqual(len(report["tool_calls"]), 3)
        self.assertGreaterEqual(len(report["evidence"]), 4)
        self.assertGreaterEqual(len(report["hypotheses"]), 1)
        self.assertFalse(report["safety"]["automatic_action_taken"])
        self.assertFalse(report["safety"]["writes_allowed"])

    def test_resolved_event_does_not_query_tools(self) -> None:
        self.payload["status"] = "resolved"
        self.payload["alerts"][0]["status"] = "resolved"
        report = self.harness.handle_webhook(self.payload)[0]

        self.assertEqual(report["event_status"], "resolved")
        self.assertEqual(report["tool_calls"], [])

    def test_namespace_allowlist_blocks_queries(self) -> None:
        self.payload["alerts"][0]["labels"]["namespace"] = "kube-system"
        report = self.harness.handle_webhook(self.payload)[0]

        self.assertIn("不在诊断允许列表", report["summary"])
        self.assertEqual(report["tool_calls"], [])

    def test_invalid_webhook_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.harness.handle_webhook({"status": "firing", "alerts": []})


if __name__ == "__main__":
    unittest.main()

