import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from frame_slicer.errors import QueueWatchError
from frame_slicer.transport import MAX_RESPONSE_BYTES, http_post, normalize_base_url


class TestNormalizeBaseUrl:
    def test_keeps_a_plain_http_url(self) -> None:
        assert normalize_base_url("http://localhost:8000") == "http://localhost:8000"

    def test_drops_a_trailing_slash(self) -> None:
        assert normalize_base_url("https://queue.example/") == "https://queue.example"

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "localhost:8000",
            "ftp://host",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://",
        ],
    )
    def test_rejects_anything_that_is_not_an_http_address(self, url: str) -> None:
        with pytest.raises(QueueWatchError, match="http"):
            normalize_base_url(url)


class _Handler(BaseHTTPRequestHandler):
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 - the name is fixed by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        _Handler.seen.append(
            {"path": self.path, "body": body, "type": self.headers["Content-Type"]}
        )
        if self.path == "/jobs":
            self._reply(201, {"id": 7, "name": body["name"], "status": "pending"})
        elif self.path == "/bad/jobs":
            self._reply(422, {"detail": "invalid"})
        elif self.path == "/moved/jobs":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
        elif self.path == "/text/jobs":
            self._raw(200, b"not json")
        elif self.path == "/list/jobs":
            self._reply(200, [1, 2, 3])
        elif self.path == "/huge/jobs":
            padding = "x" * (MAX_RESPONSE_BYTES + 10)
            self._reply(200, {"id": 1, "status": "pending", "padding": padding})
        elif self.path == "/slow/jobs":
            time.sleep(1)
            self._reply(201, {"id": 1, "status": "pending"})
        else:
            self._reply(404, {})

    def _reply(self, status: int, payload: Any) -> None:
        self._raw(status, json.dumps(payload).encode())

    def _raw(self, status: int, body: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


@pytest.fixture()
def server() -> Iterator[str]:
    _Handler.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


class TestHttpPost:
    def test_sends_the_payload_as_json_and_returns_the_answer(self, server: str) -> None:
        answer = http_post(f"{server}/jobs", {"name": "A_1-2", "priority": "high"})
        assert answer == {"id": 7, "name": "A_1-2", "status": "pending"}
        assert _Handler.seen == [
            {
                "path": "/jobs",
                "body": {"name": "A_1-2", "priority": "high"},
                "type": "application/json",
            }
        ]

    def test_reports_the_status_when_the_server_refuses(self, server: str) -> None:
        with pytest.raises(QueueWatchError, match="422"):
            http_post(f"{server}/bad/jobs", {"name": "A", "priority": "normal"})

    def test_does_not_follow_a_redirect(self, server: str) -> None:
        with pytest.raises(QueueWatchError, match="302"):
            http_post(f"{server}/moved/jobs", {"name": "A", "priority": "normal"})

    def test_reports_an_answer_that_is_not_json(self, server: str) -> None:
        with pytest.raises(QueueWatchError, match="JSON"):
            http_post(f"{server}/text/jobs", {"name": "A", "priority": "normal"})

    def test_reports_an_answer_that_is_not_an_object(self, server: str) -> None:
        with pytest.raises(QueueWatchError, match="JSON object"):
            http_post(f"{server}/list/jobs", {"name": "A", "priority": "normal"})

    def test_reports_a_server_that_cannot_be_reached(self) -> None:
        with pytest.raises(QueueWatchError, match="could not reach"):
            http_post("http://127.0.0.1:1/jobs", {"name": "A", "priority": "normal"})

    def test_names_the_address_in_the_unreachable_error(self) -> None:
        with pytest.raises(QueueWatchError, match="127.0.0.1:1"):
            http_post("http://127.0.0.1:1/jobs", {"name": "A", "priority": "normal"})

    def test_reads_only_a_limited_part_of_an_answer(self, server: str) -> None:
        with pytest.raises(QueueWatchError, match="JSON"):
            http_post(f"{server}/huge/jobs", {"name": "A", "priority": "normal"})

    def test_gives_up_on_a_server_that_answers_too_slowly(
        self, server: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("frame_slicer.transport.TIMEOUT_SECONDS", 0.2)
        with pytest.raises(QueueWatchError, match="could not reach"):
            http_post(f"{server}/slow/jobs", {"name": "A", "priority": "normal"})
