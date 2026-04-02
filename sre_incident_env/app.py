from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .client import IncidentClient


CLIENT = IncidentClient()


class IncidentHandler(BaseHTTPRequestHandler):
    server_version = "SREIncidentEnv/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/state":
            self._send_json(HTTPStatus.OK, CLIENT.state())
            return
        if self.path == "/scenarios":
            self._send_json(HTTPStatus.OK, CLIENT.scenarios())
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        body = self._read_json()
        try:
            if self.path == "/reset":
                payload = CLIENT.reset(
                    scenario_id=body.get("scenario_id"),
                    budget=body.get("budget"),
                )
                self._send_json(HTTPStatus.OK, payload)
                return
            if self.path == "/step":
                payload = CLIENT.step(body.get("action", {}))
                self._send_json(HTTPStatus.OK, payload)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except Exception as exc:  # pragma: no cover - surfaced to callers
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _send_json(self, status: HTTPStatus, payload: object) -> None:
        encoded = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), IncidentHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
