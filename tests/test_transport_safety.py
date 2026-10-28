"""Timeouts, retries, double-submission guard, and the token handling of the transport."""

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from frame_slicer.errors import QueueWatchError
from frame_slicer.transport import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_CAP_SECONDS,
    MAX_ATTEMPTS,
    MAX_RESPONSE_BYTES,
    backoff_delay,
    http_post,
    idempotency_key,
)

PAYLOAD = {"name": "A_1-2", "priority": "normal"}


class _Script(BaseHTTPRequestHandler):
    """Answers with the next scripted (status, headers) and records every request."""

    script: list[tuple[int, dict[str, str]]] = []
    requests: list[dict[str, str | None]] = []

    def do_POST(self) -> None:  # noqa: N802 - the name is fixed by BaseHTTPRequestHandler
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        _Script.requests.append(
            {
                "authorization": self.headers.get("Authorization"),
                "key": self.headers.get("Idempotency-Key"),
            }
        )
        status, headers = _Script.script.pop(0) if _Script.script else (201, {})
        body = json.dumps({"id": 1, "status": "pending"}).encode()
        if self.path == "/slow":
            time.sleep(1)
        try:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


@pytest.fixture()
def server() -> Iterator[str]:
    _Script.script = []
    _Script.requests = []
    httpd = HTTPServer(("127.0.0.1", 0), _Script)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class TestBackoffDelay:
    def test_is_zero_when_the_jitter_draw_is_zero(self) -> None:
        assert backoff_delay(0, lambda: 0.0) == 0.0

    def test_grows_with_each_attempt_up_to_the_cap(self) -> None:
        full = [backoff_delay(n, lambda: 1.0) for n in range(8)]
        assert full[0] == BACKOFF_BASE_SECONDS
        assert full[1] == BACKOFF_BASE_SECONDS * 2
        assert max(full) == BACKOFF_CAP_SECONDS

    def test_never_exceeds_the_cap(self) -> None:
        assert backoff_delay(50, lambda: 1.0) == BACKOFF_CAP_SECONDS


class TestIdempotencyKey:
    def test_is_the_same_for_the_same_job(self) -> None:
        assert idempotency_key("http://q/jobs", PAYLOAD) == idempotency_key(
            "http://q/jobs", dict(reversed(list(PAYLOAD.items())))
        )

    def test_differs_for_another_job(self) -> None:
        other = {**PAYLOAD, "name": "A_3-4"}
        assert idempotency_key("http://q/jobs", PAYLOAD) != idempotency_key("http://q/jobs", other)

    def test_differs_for_another_queue(self) -> None:
        assert idempotency_key("http://q/jobs", PAYLOAD) != idempotency_key(
            "http://other/jobs", PAYLOAD
        )


class TestRetry:
    def test_retries_a_503_and_then_succeeds(self, server: str) -> None:
        _Script.script = [(503, {}), (503, {}), (201, {})]
        sleeps = Sleeps()
        answer = http_post(f"{server}/jobs", PAYLOAD, sleep=sleeps, jitter=lambda: 1.0)
        assert answer["id"] == 1
        assert len(_Script.requests) == 3
        assert sleeps.delays == [BACKOFF_BASE_SECONDS, BACKOFF_BASE_SECONDS * 2]

    def test_retries_a_429_and_honours_retry_after(self, server: str) -> None:
        _Script.script = [(429, {"Retry-After": "3"}), (201, {})]
        sleeps = Sleeps()
        http_post(f"{server}/jobs", PAYLOAD, sleep=sleeps)
        assert sleeps.delays == [3.0]

    def test_caps_a_very_long_retry_after(self, server: str) -> None:
        _Script.script = [(429, {"Retry-After": "86400"}), (201, {})]
        sleeps = Sleeps()
        http_post(f"{server}/jobs", PAYLOAD, sleep=sleeps)
        assert sleeps.delays == [30.0]

    def test_ignores_a_retry_after_it_cannot_read(self, server: str) -> None:
        _Script.script = [(503, {"Retry-After": "soon"}), (201, {})]
        sleeps = Sleeps()
        http_post(f"{server}/jobs", PAYLOAD, sleep=sleeps, jitter=lambda: 1.0)
        assert sleeps.delays == [BACKOFF_BASE_SECONDS]

    def test_gives_up_after_the_maximum_number_of_attempts(self, server: str) -> None:
        _Script.script = [(503, {})] * (MAX_ATTEMPTS + 2)
        with pytest.raises(QueueWatchError, match=rf"503.*{MAX_ATTEMPTS} attempts"):
            http_post(f"{server}/jobs", PAYLOAD, sleep=Sleeps())
        assert len(_Script.requests) == MAX_ATTEMPTS

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 500])
    def test_does_not_retry_other_refusals(self, server: str, status: int) -> None:
        _Script.script = [(status, {}), (201, {})]
        sleeps = Sleeps()
        with pytest.raises(QueueWatchError, match=str(status)):
            http_post(f"{server}/jobs", PAYLOAD, sleep=sleeps)
        assert len(_Script.requests) == 1
        assert sleeps.delays == []

    def test_retries_a_connection_that_was_never_made(self) -> None:
        sleeps = Sleeps()
        with pytest.raises(QueueWatchError, match="could not reach"):
            http_post("http://127.0.0.1:1/jobs", PAYLOAD, sleep=sleeps)
        assert len(sleeps.delays) == MAX_ATTEMPTS - 1

    def test_does_not_retry_a_slow_answer_because_the_job_may_already_exist(
        self, server: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("frame_slicer.transport.TIMEOUT_SECONDS", 0.2)
        sleeps = Sleeps()
        with pytest.raises(QueueWatchError, match="could not reach"):
            http_post(f"{server}/slow", PAYLOAD, sleep=sleeps)
        assert len(_Script.requests) == 1
        assert sleeps.delays == []

    def test_sends_the_same_idempotency_key_on_every_attempt(self, server: str) -> None:
        _Script.script = [(503, {}), (201, {})]
        http_post(f"{server}/jobs", PAYLOAD, sleep=Sleeps())
        keys = [request["key"] for request in _Script.requests]
        assert keys[0] == keys[1]
        assert keys[0] == idempotency_key(f"{server}/jobs", PAYLOAD)


class TestToken:
    def test_sends_the_token_as_a_bearer_credential(self, server: str) -> None:
        http_post(f"{server}/jobs", PAYLOAD, token="s3cret-token-value")
        assert _Script.requests[0]["authorization"] == "Bearer s3cret-token-value"

    def test_sends_no_credential_without_a_token(self, server: str) -> None:
        http_post(f"{server}/jobs", PAYLOAD)
        assert _Script.requests[0]["authorization"] is None

    def test_does_not_put_the_token_in_an_error_message(self, server: str) -> None:
        _Script.script = [(401, {})]
        with pytest.raises(QueueWatchError) as caught:
            http_post(f"{server}/jobs", PAYLOAD, token="s3cret-token-value")
        assert "s3cret-token-value" not in str(caught.value)

    def test_refuses_to_send_a_token_over_plain_http_to_another_host(self) -> None:
        with pytest.raises(QueueWatchError, match="https"):
            http_post("http://queue.example/jobs", PAYLOAD, token="s3cret-token-value")

    def test_allows_a_token_over_https(self) -> None:
        with pytest.raises(QueueWatchError, match="could not reach"):
            http_post(
                "https://127.0.0.1:1/jobs", PAYLOAD, token="s3cret-token-value", sleep=Sleeps()
            )

    @pytest.mark.parametrize("token", ["a\nb", "a\r\nX-Evil: 1", "tab\there"])
    def test_refuses_a_token_that_could_inject_a_header(self, token: str) -> None:
        with pytest.raises(QueueWatchError, match="control characters"):
            http_post("http://127.0.0.1:1/jobs", PAYLOAD, token=token)


class TestResponseCap:
    def test_says_so_when_an_answer_is_larger_than_the_limit(self, server: str) -> None:
        assert MAX_RESPONSE_BYTES > 0
        _Script.script = []
        with pytest.raises(QueueWatchError, match="larger than"):
            http_post(f"{server}/huge", PAYLOAD, read_limit=10)
