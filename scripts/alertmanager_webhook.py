#!/usr/bin/env python3
"""A zero-dependency Alertmanager webhook inspector for local debugging."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


MAX_BODY_BYTES = 1024 * 1024
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_headers(
    headers: dict[str, str],
    show_sensitive: bool,
) -> dict[str, str]:
    if show_sensitive:
        return headers
    return {
        name: "<redacted>" if name.lower() in SENSITIVE_HEADERS else value
        for name, value in headers.items()
    }


def alert_summary(payload: dict[str, Any]) -> dict[str, Any]:
    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        alerts = []
    return {
        "version": payload.get("version"),
        "status": payload.get("status"),
        "receiver": payload.get("receiver"),
        "groupKey": payload.get("groupKey"),
        "truncatedAlerts": payload.get("truncatedAlerts"),
        "externalURL": payload.get("externalURL"),
        "groupLabels": payload.get("groupLabels", {}),
        "commonLabels": payload.get("commonLabels", {}),
        "commonAnnotations": payload.get("commonAnnotations", {}),
        "alertCount": len(alerts),
        "alerts": [
            {
                "status": alert.get("status"),
                "labels": alert.get("labels", {}),
                "annotations": alert.get("annotations", {}),
                "startsAt": alert.get("startsAt"),
                "endsAt": alert.get("endsAt"),
                "generatorURL": alert.get("generatorURL"),
                "fingerprint": alert.get("fingerprint"),
            }
            for alert in alerts
            if isinstance(alert, dict)
        ],
    }


def print_request(envelope: dict[str, Any]) -> None:
    separator = "=" * 88
    print(f"\n{separator}", flush=True)
    print(f"request_id : {envelope['request_id']}", flush=True)
    print(f"received_at: {envelope['received_at']}", flush=True)
    print(f"client     : {envelope['client']}", flush=True)
    print(f"request    : {envelope['method']} {envelope['path']}", flush=True)
    print("\n--- headers ---", flush=True)
    print(
        json.dumps(envelope["headers"], ensure_ascii=False, indent=2),
        flush=True,
    )
    print("\n--- raw body ---", flush=True)
    print(envelope["raw_body"], flush=True)
    print("\n--- parsed JSON ---", flush=True)
    print(
        json.dumps(envelope["payload"], ensure_ascii=False, indent=2),
        flush=True,
    )
    print("\n--- Alertmanager summary ---", flush=True)
    print(
        json.dumps(envelope["summary"], ensure_ascii=False, indent=2),
        flush=True,
    )
    print(separator, flush=True)


class WebhookServer(HTTPServer):
    output: Path | None
    show_sensitive_headers: bool


class Handler(BaseHTTPRequestHandler):
    server: WebhookServer

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.send_json(200, {"status": "ok"})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        request_id = f"am-{uuid.uuid4().hex}"
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "invalid Content-Length"})
            return

        if content_length <= 0:
            self.send_json(400, {"error": "request body is empty"})
            return
        if content_length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "request body exceeds 1 MiB"})
            return

        raw_bytes = self.rfile.read(content_length)
        raw_body = raw_bytes.decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            print(f"invalid JSON body: {raw_body}", file=sys.stderr, flush=True)
            self.send_json(
                400,
                {
                    "error": "invalid JSON",
                    "detail": str(exc),
                    "request_id": request_id,
                },
            )
            return

        if not isinstance(payload, dict):
            self.send_json(
                400,
                {
                    "error": "JSON root must be an object",
                    "request_id": request_id,
                },
            )
            return

        headers = redact_headers(
            dict(self.headers.items()),
            self.server.show_sensitive_headers,
        )
        envelope = {
            "request_id": request_id,
            "received_at": utc_now(),
            "client": {
                "host": self.client_address[0],
                "port": self.client_address[1],
            },
            "method": self.command,
            "path": self.path,
            "headers": headers,
            "raw_body": raw_body,
            "payload": payload,
            "summary": alert_summary(payload),
        }
        print_request(envelope)
        self.write_jsonl(envelope)
        self.send_json(
            200,
            {
                "status": "accepted",
                "request_id": request_id,
                "alerts_received": envelope["summary"]["alertCount"],
            },
        )

    def write_jsonl(self, envelope: dict[str, Any]) -> None:
        if self.server.output is None:
            return
        with self.server.output.open("a", encoding="utf-8") as output:
            output.write(json.dumps(envelope, ensure_ascii=False) + "\n")

    def send_json(self, status: int, body: dict[str, Any]) -> None:
        response = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: Any) -> None:
        print(
            f"{self.address_string()} - {format % args}",
            file=sys.stderr,
            flush=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print complete Alertmanager webhook requests."
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="listen address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=18080,
        help="listen port (default: 18080)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="append complete request envelopes to a JSONL file",
    )
    parser.add_argument(
        "--show-sensitive-headers",
        action="store_true",
        help="print Authorization, Cookie and API key headers without redaction",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="handle one HTTP request and exit (useful for testing)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = WebhookServer((args.host, args.port), Handler)
    server.output = args.output
    server.show_sensitive_headers = args.show_sensitive_headers
    print(
        f"Alertmanager webhook inspector listening on "
        f"http://{args.host}:{args.port}",
        flush=True,
    )
    print("POST endpoint: /alerts", flush=True)
    print("Health check : /healthz", flush=True)
    if args.output:
        print(f"JSONL output : {args.output}", flush=True)
    try:
        if args.once:
            server.handle_request()
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping webhook inspector", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

