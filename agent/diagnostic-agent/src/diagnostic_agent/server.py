from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import Config
from .harness import DiagnosticHarness, ValidationError
from .tools import ReadOnlyTools


LOGGER = logging.getLogger("diagnostic-agent")


class Handler(BaseHTTPRequestHandler):
    harness: DiagnosticHarness

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._json_response(200, {"status": "ok"})
            return
        self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/api/v1/alerts":
            self._json_response(404, {"error": "not found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 1_048_576:
                raise ValidationError("body size must be between 1 byte and 1 MiB")
            payload = json.loads(self.rfile.read(content_length))
            reports = self.harness.handle_webhook(payload)
            for report in reports:
                LOGGER.info(
                    json.dumps(
                        {"event": "diagnostic_report", "report": report},
                        ensure_ascii=False,
                    )
                )
            self._json_response(202, {"reports": reports})
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            self._json_response(400, {"error": str(exc)})
        except Exception:
            LOGGER.exception("unhandled diagnostic error")
            self._json_response(500, {"error": "internal server error"})

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.client_address[0], format % args)

    def _json_response(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = Config.from_env()
    Handler.harness = DiagnosticHarness(config, ReadOnlyTools(config))
    server = ThreadingHTTPServer((config.host, config.port), Handler)
    LOGGER.info("listening on %s:%s", config.host, config.port)
    server.serve_forever()


if __name__ == "__main__":
    main()

