"""Independent HTTP event store for publication adapter operation fixtures."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from threading import Lock
from urllib.parse import urlsplit
from uuid import UUID


EVENT_SCHEMA = "salience.adapter-operation.v1"
EVENT_FIELDS = frozenset(
    {"schema", "run_id", "worker_id", "sequence", "operation"}
)
OPERATIONS = frozenset(
    {
        "capabilities",
        "preflight",
        "prepare_delivery",
        "create_or_resume",
        "submit",
        "reconcile",
        "status",
        "cancel",
        "verify_webhook",
        "refresh_capabilities",
    }
)


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _valid_event(event: object) -> bool:
    if not isinstance(event, dict) or set(event) != EVENT_FIELDS:
        return False
    operation = event.get("operation")
    return (
        event.get("schema") == EVENT_SCHEMA
        and _canonical_uuid(event.get("run_id"))
        and _canonical_uuid(event.get("worker_id"))
        and isinstance(event.get("sequence"), int)
        and not isinstance(event.get("sequence"), bool)
        and event["sequence"] > 0
        and isinstance(operation, str)
        and operation in OPERATIONS
    )


class _OperationEventServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.events_by_run: dict[str, list[dict[str, object]]] = {}
        self.events_lock = Lock()

    def add_event(self, event: dict[str, object]) -> bool:
        run_id = event["run_id"]
        sequence = event["sequence"]
        with self.events_lock:
            events = self.events_by_run.setdefault(run_id, [])
            if any(item["sequence"] == sequence for item in events):
                return False
            events.append(event)
        return True

    def events_for_run(self, run_id: str) -> list[dict[str, object]]:
        with self.events_lock:
            events = list(self.events_by_run.get(run_id, ()))
        return sorted(events, key=lambda event: event["sequence"])


class _OperationEventHandler(BaseHTTPRequestHandler):
    server: _OperationEventServer

    def _respond(self, status: int, payload: dict[str, object]) -> None:
        body = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/v1/adapter-events":
            self._respond(404, {"error": "not-found"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
            if length <= 0 or length > 4096:
                raise ValueError("invalid body length")
            event = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._respond(400, {"error": "invalid-event"})
            return
        if not _valid_event(event):
            self._respond(400, {"error": "invalid-event"})
            return
        if not self.server.add_event(event):
            self._respond(409, {"error": "duplicate-sequence"})
            return
        self._respond(202, {"accepted": True})

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        match = re.fullmatch(r"/v1/runs/([0-9a-f-]{36})/events", parsed.path)
        if match is None or parsed.query or parsed.fragment:
            self._respond(404, {"error": "not-found"})
            return
        run_id = match.group(1)
        if not _canonical_uuid(run_id):
            self._respond(404, {"error": "not-found"})
            return
        self._respond(
            200,
            {"run_id": run_id, "events": self.server.events_for_run(run_id)},
        )

    def log_message(self, _format: str, *_args: object) -> None:
        # Operation fixtures contain no request details worth logging.
        return


def create_server(host: str = "0.0.0.0", port: int = 8081) -> ThreadingHTTPServer:
    """Create an independent, in-memory event receiver for a fixture run."""
    return _OperationEventServer((host, port), _OperationEventHandler)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    arguments = parser.parse_args(argv)
    server = create_server(arguments.host, arguments.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
