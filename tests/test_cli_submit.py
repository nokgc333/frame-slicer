from typing import Any

from click.testing import CliRunner

from frame_slicer.cli import main
from frame_slicer.queuewatch import QueueWatchError


class FakeQueue:
    def __init__(self, fail_on: int | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_on = fail_on

    def __call__(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((url, payload))
        if self.fail_on == len(self.calls):
            raise QueueWatchError("could not reach the queue")
        return {"id": 100 + len(self.calls), "status": "pending"}


def run(queue: FakeQueue | None, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, ["submit", *args], obj=queue)
    return result.exit_code, result.output


BASE = ("1001-1240", "--slices", "8", "--name", "SHOT010_cache")


class TestSubmit:
    def test_submits_one_job_per_slice_and_prints_each_answer(self) -> None:
        queue = FakeQueue()
        code, output = run(queue, *BASE)
        assert code == 0
        lines = output.splitlines()
        assert lines[0] == "submitted SHOT010_cache_1001-1030 (id=101, status=pending)"
        assert lines[7] == "submitted SHOT010_cache_1211-1240 (id=108, status=pending)"
        assert lines[8] == "8 of 8 jobs submitted"
        assert len(queue.calls) == 8

    def test_posts_to_the_local_queue_with_normal_priority_by_default(self) -> None:
        queue = FakeQueue()
        run(queue, *BASE)
        assert queue.calls[0] == (
            "http://localhost:8000/jobs",
            {"name": "SHOT010_cache_1001-1030", "priority": "normal"},
        )

    def test_uses_the_address_and_priority_it_is_given(self) -> None:
        queue = FakeQueue()
        run(queue, *BASE, "--priority", "high", "--api-base-url", "https://queue.example")
        assert queue.calls[0][0] == "https://queue.example/jobs"
        assert queue.calls[0][1]["priority"] == "high"

    def test_sends_nothing_in_a_dry_run_and_lists_what_it_would_send(self) -> None:
        queue = FakeQueue()
        code, output = run(queue, *BASE, "--dry-run")
        assert code == 0
        assert queue.calls == []
        lines = output.splitlines()
        assert lines[0] == "[dry-run] SHOT010_cache_1001-1030 (priority=normal)"
        assert len(lines) == 8 + 1
        assert lines[8] == "[dry-run] 8 jobs would be submitted to http://localhost:8000"

    def test_fails_with_exit_code_1_when_the_queue_cannot_be_reached(self) -> None:
        queue = FakeQueue(fail_on=1)
        code, output = run(queue, *BASE)
        assert code == 1
        assert "could not reach the queue" in output
        assert "0 of 8 jobs submitted" in output

    def test_reports_how_many_jobs_went_through_before_a_failure(self) -> None:
        queue = FakeQueue(fail_on=3)
        code, output = run(queue, *BASE)
        assert code == 1
        assert "submitted SHOT010_cache_1001-1030" in output
        assert "submitted SHOT010_cache_1031-1060" in output
        assert "submitted SHOT010_cache_1061-1090" not in output
        assert "2 of 8 jobs submitted" in output
        assert len(queue.calls) == 3

    def test_fails_with_a_usage_error_when_the_range_is_wrong(self) -> None:
        queue = FakeQueue()
        code, output = run(queue, "abc", "--slices", "2", "--name", "A")
        assert code == 2
        assert "START-END" in output
        assert queue.calls == []

    def test_fails_with_a_usage_error_when_the_name_is_missing(self) -> None:
        code, output = run(FakeQueue(), "1-10", "--slices", "2")
        assert code == 2
        assert "--name" in output

    def test_fails_with_a_usage_error_when_the_name_has_a_control_character(self) -> None:
        queue = FakeQueue()
        code, output = run(queue, "1-10", "--slices", "2", "--name", "a\nb")
        assert code == 2
        assert "control" in output
        assert queue.calls == []

    def test_fails_with_a_usage_error_when_the_priority_is_unknown(self) -> None:
        code, output = run(FakeQueue(), "1-10", "--slices", "2", "--name", "A", "--priority", "x")
        assert code == 2
        assert "priority" in output.lower()

    def test_fails_with_a_usage_error_when_the_address_is_not_http(self) -> None:
        queue = FakeQueue()
        code, output = run(
            queue, "1-10", "--slices", "2", "--name", "A", "--api-base-url", "file:///etc"
        )
        assert code == 2
        assert "http" in output
        assert queue.calls == []

    def test_checks_the_address_even_in_a_dry_run(self) -> None:
        code, _ = run(
            FakeQueue(),
            "1-10",
            "--slices",
            "2",
            "--name",
            "A",
            "--dry-run",
            "--api-base-url",
            "ftp://x",
        )
        assert code == 2

    def test_lists_the_submit_command_in_the_help(self) -> None:
        result = CliRunner().invoke(main, ["--help"])
        assert "submit" in result.output

    def test_prints_the_error_on_stderr_and_only_submitted_jobs_on_stdout(self) -> None:
        result = CliRunner().invoke(main, ["submit", *BASE], obj=FakeQueue(fail_on=2))
        assert "Error: could not reach the queue" in result.stderr
        assert "1 of 8 jobs submitted" in result.stderr
        assert "Error" not in result.stdout
        assert result.stdout.splitlines() == [
            "submitted SHOT010_cache_1001-1030 (id=101, status=pending)"
        ]
