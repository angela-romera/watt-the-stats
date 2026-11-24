"""Run the import workflow on demand over HTTP instead of the scheduler."""

import hmac
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from dotenv import load_dotenv
from loguru import logger

from main import main as run_import
from src.config import PROJECT_ROOT

MODES: dict[str, list[str]] = {
    "upload": [],
    "process-only": ["--process-only"],
    "send-report": ["--send-report"],
    "send-empty-report": ["--send-empty-report"],
}
DEFAULT_MODE = "upload"
_RUN_LOCK = threading.Lock()


class ImportApiServer(ThreadingHTTPServer):
    """Store the bearer token required by the run endpoint."""

    api_token: str


class RunRequestHandler(BaseHTTPRequestHandler):
    """Serve health checks and on-demand import requests."""

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        self._send_json(HTTPStatus.OK, {"status": "ok"})

    def do_POST(self) -> None:
        if self.path != "/run":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if not self._authorized():
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "Unauthorized"},
                {"WWW-Authenticate": "Bearer"},
            )
            return
        mode = self._read_mode()
        if mode is None:
            return
        if not _RUN_LOCK.acquire(blocking=False):
            self._send_json(HTTPStatus.CONFLICT, {"error": "A run is already in progress"})
            return
        try:
            logger.info("Starting on-demand run: mode={}", mode)
            run_import(MODES[mode])
        except (Exception, SystemExit):
            logger.exception("On-demand run failed: mode={}", mode)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"status": "failed"})
        else:
            logger.info("On-demand run finished: mode={}", mode)
            self._send_json(HTTPStatus.OK, {"status": "ok", "mode": mode})
        finally:
            _RUN_LOCK.release()

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("HTTP {}", format % args)

    def _authorized(self) -> bool:
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(
            token.strip(), self.server.api_token
        )

    def _read_mode(self) -> str | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
            return None
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return DEFAULT_MODE
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Body must be valid JSON"})
            return None
        if not isinstance(payload, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Body must be a JSON object"})
            return None
        mode = payload.get("mode", DEFAULT_MODE)
        if not isinstance(mode, str) or mode not in MODES:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Unknown mode: {mode!r}. Choose one of {sorted(MODES)}"},
            )
            return None
        return mode

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def create_server(host: str, port: int, token: str) -> ImportApiServer:
    """Create the HTTP server that rejects requests without the given token."""
    server = ImportApiServer((host, port), RunRequestHandler)
    server.api_token = token
    return server


def load_api_token() -> str:
    """Require the bearer token that protects the run endpoint."""
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("API_TOKEN", "").strip()
    if not token:
        raise ValueError("API_TOKEN must be set to start the import API")
    return token


def main() -> None:
    """Serve the on-demand import API until interrupted."""
    token = load_api_token()
    server = create_server("0.0.0.0", 8000, token)
    logger.info("Serving on-demand import API on port 8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping on-demand import API")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
