import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from frame_slicer.queuewatch import (
    MAX_NAME_LENGTH,
    MAX_RESPONSE_BYTES,
    JobRequest,
    QueueWatchError,
    build_jobs,
    http_post,
    normalize_base_url,
    submit_jobs,
)
from frame_slicer.ranges import FrameRange


class TestBuildJobs:
    def test_names_each_job_after_its_frame_range(self) -> None:
        jobs = build_jobs("SHOT010_cache", [FrameRange(1001, 1030), FrameRange(1031, 1060)], "high")
        assert jobs == [
            JobRequest("SHOT010_cache_1001-1030", "high"),
            JobRequest("SHOT010_cache_1031-1060", "high"),
        ]

    def test_trims_spaces_around_the_name(self) -> None:
        assert build_jobs("  A  ", [FrameRange(1, 2)], "normal")[0].name == "A_1-2"

    @pytest.mark.parametrize("name", ["", "   "])
    def test_rejects_an_empty_name(self, name: str) -> None:
        with pytest.raises(QueueWatchError, match="name"):
            build_jobs(name, [FrameRange(1, 2)], "normal")

    @pytest.mark.parametrize("name", ["a\nb", "a\tb", "a\x00b", "a\x7fb"])
    def test_rejects_control_characters_in_the_name(self, name: str) -> None:
        with pytest.raises(QueueWatchError, match="control"):
            build_jobs(name, [FrameRange(1, 2)], "normal")

    def test_rejects_a_job_name_longer_than_the_queue_accepts(self) -> None:
        longest_ok = "x" * (MAX_NAME_LENGTH - len("_1-2"))
        assert len(build_jobs(longest_ok, [FrameRange(1, 2)], "normal")[0].name) == MAX_NAME_LENGTH
        with pytest.raises(QueueWatchError, match=str(MAX_NAME_LENGTH)):
            build_jobs(longest_ok + "x", [FrameRange(1, 2)], "normal")

    def test_rejects_an_unknown_priority(self) -> None:
        with pytest.raises(QueueWatchError, match="priority"):
            build_jobs("A", [FrameRange(1, 2)], "urgent")

    @pytest.mark.parametrize("priority", ["high", "normal", "low"])
    def test_accepts_every_queue_priority(self, priority: str) -> None:
        assert build_jobs("A", [FrameRange(1, 2)], priority)[0].priority == priority


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


class TestSubmitJobs:
    def test_posts_every_job_to_the_jobs_endpoint_in_order(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append((url, payload))
            return {"id": len(calls), "status": "pending"}

        jobs = [JobRequest("A_1-2", "normal"), JobRequest("A_3-4", "normal")]
        results = list(submit_jobs("http://host:8000/", jobs, post))

        assert [url for url, _ in calls] == ["http://host:8000/jobs"] * 2
        assert [payload for _, payload in calls] == [
            {"name": "A_1-2", "priority": "normal"},
            {"name": "A_3-4", "priority": "normal"},
        ]
        assert [(r.name, r.id, r.status) for r in results] == [
            ("A_1-2", 1, "pending"),
            ("A_3-4", 2, "pending"),
        ]

    def test_hands_back_each_job_as_soon_as_it_is_submitted(self) -> None:
        seen: list[str] = []

        def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            seen.append(payload["name"])
            return {"id": 1, "status": "pending"}

        iterator = submit_jobs("http://h", [JobRequest("A", "low"), JobRequest("B", "low")], post)
        next(iterator)
        assert seen == ["A"]

    def test_stops_at_the_first_failure_without_sending_the_rest(self) -> None:
        sent: list[str] = []

        def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            sent.append(payload["name"])
            if payload["name"] == "B":
                raise QueueWatchError("boom")
            return {"id": 1, "status": "pending"}

        jobs = [JobRequest(n, "normal") for n in ("A", "B", "C")]
        iterator = submit_jobs("http://h", jobs, post)
        assert next(iterator).name == "A"
        with pytest.raises(QueueWatchError, match="boom"):
            next(iterator)
        assert sent == ["A", "B"]

    def test_rejects_an_unusable_address_before_sending_anything(self) -> None:
        sent: list[str] = []

        def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            sent.append(url)
            return {"id": 1, "status": "pending"}

        with pytest.raises(QueueWatchError, match="http"):
            list(submit_jobs("ftp://h", [JobRequest("A", "normal")], post))
        assert sent == []

    @pytest.mark.parametrize(
        "answer",
        [
            {"id": True, "status": "pending"},
            {"id": "7", "status": "pending"},
            {"id": 7, "status": None},
        ],
    )
    def test_rejects_an_answer_whose_id_or_status_has_the_wrong_type(
        self, answer: dict[str, Any]
    ) -> None:
        def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            return answer

        with pytest.raises(QueueWatchError, match="unexpected"):
            list(submit_jobs("http://h", [JobRequest("A", "normal")], post))

    def test_rejects_an_answer_without_an_id_or_status(self) -> None:
        def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"detail": "odd"}

        with pytest.raises(QueueWatchError, match="unexpected"):
            list(submit_jobs("http://h", [JobRequest("A", "normal")], post))


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
        monkeypatch.setattr("frame_slicer.queuewatch.TIMEOUT_SECONDS", 0.2)
        with pytest.raises(QueueWatchError, match="could not reach"):
            http_post(f"{server}/slow/jobs", {"name": "A", "priority": "normal"})
