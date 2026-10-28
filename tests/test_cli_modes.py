"""The split modes, the manifest, retry, status and commands, through the command line."""

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from frame_slicer.cli import main
from frame_slicer.errors import QueueWatchError
from frame_slicer.workflow import Api


class FakeQueue:
    def __init__(self) -> None:
        self.statuses: dict[int, str] = {}
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.refuse: set[str] = set()
        self.next_id = 1

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((url, payload))
        if url.endswith("/requeue"):
            job_id = int(url.split("/")[-2])
            self.statuses[job_id] = "pending"
            return {"id": job_id, "status": "pending"}
        if payload["name"] in self.refuse:
            raise QueueWatchError("could not reach the queue")
        job_id, self.next_id = self.next_id, self.next_id + 1
        self.statuses[job_id] = "pending"
        return {"id": job_id, "status": "pending"}

    def get(self, url: str) -> dict[str, Any]:
        return {"status": self.statuses[int(url.rsplit("/", 1)[1])]}

    @property
    def api(self) -> Api:
        return Api(self.post, self.get)


def run(queue: FakeQueue | None, *args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(main, list(args), obj=queue.api if queue else None)
    return result.exit_code, result.stdout, result.stderr


class TestSplitModes:
    def test_cuts_fixed_size_chunks(self) -> None:
        code, out, _ = run(None, "split", "1-10", "--chunk-size", "4")
        assert code == 0
        assert out.splitlines() == [
            "slice 1/3  1-4  (4 frames)",
            "slice 2/3  5-8  (4 frames)",
            "slice 3/3  9-10  (2 frames)",
        ]

    def test_cuts_a_stepped_range(self) -> None:
        _, out, _ = run(None, "split", "1-9x2", "--slices", "2")
        assert out.splitlines() == ["slice 1/2  1-5x2  (3 frames)", "slice 2/2  7-9x2  (2 frames)"]

    def test_cuts_several_ranges(self) -> None:
        _, out, _ = run(None, "split", "1-4,11-12", "--chunk-size", "2")
        assert [line.split("  ")[1] for line in out.splitlines()] == ["1-2", "3-4", "11-12"]

    def test_puts_the_heavy_slices_first(self) -> None:
        _, out, _ = run(None, "split", "1-30", "--slices", "3", "--heavy", "25-26")
        assert [line.split("  ")[1] for line in out.splitlines()] == ["21-30", "1-10", "11-20"]

    def test_says_when_both_ways_to_cut_are_given(self) -> None:
        code, _, err = run(None, "split", "1-10", "--slices", "2", "--chunk-size", "2")
        assert code == 2 and "either" in err

    def test_says_when_a_heavy_range_is_wrong(self) -> None:
        code, _, err = run(None, "split", "1-10", "--slices", "2", "--heavy", "x")
        assert code == 2 and "frame range" in err


class TestSubmitWithManifest:
    ARGS = ("submit", "1-30", "--slices", "3", "--name", "S", "--scene", "a.blend")

    def test_sends_the_range_and_scene_and_writes_the_manifest(self, tmp_path: Path) -> None:
        queue, path = FakeQueue(), tmp_path / "m.json"
        code, out, _ = run(queue, *self.ARGS, "--manifest", str(path))
        assert code == 0 and out.splitlines()[-1] == "3 of 3 jobs submitted"
        assert queue.posts[1][1] == {
            "name": "S_11-20",
            "priority": "normal",
            "frame_start": 11,
            "frame_end": 20,
            "scene": "a.blend",
        }
        data = json.loads(path.read_text(encoding="utf-8"))
        assert [j["id"] for j in data["jobs"]] == [1, 2, 3]

    def test_keeps_the_manifest_when_a_job_fails_and_points_to_retry(self, tmp_path: Path) -> None:
        queue, path = FakeQueue(), tmp_path / "m.json"
        queue.refuse = {"S_11-20"}
        code, _, err = run(queue, *self.ARGS, "--manifest", str(path))
        assert code == 1
        assert f"frame-slicer retry {path}" in err
        assert "1 of 3 jobs submitted" in err
        data = json.loads(path.read_text(encoding="utf-8"))
        assert [j["id"] for j in data["jobs"]] == [1, None, None]

    def test_refuses_to_overwrite_an_existing_manifest(self, tmp_path: Path) -> None:
        queue, path = FakeQueue(), tmp_path / "m.json"
        path.write_text("{}", encoding="utf-8")
        code, _, err = run(queue, *self.ARGS, "--manifest", str(path))
        assert code == 2 and "retry" in err
        assert queue.posts == []
        assert path.read_text(encoding="utf-8") == "{}"

    def test_says_when_the_manifest_cannot_be_written(self, tmp_path: Path) -> None:
        queue = FakeQueue()
        code, _, err = run(queue, *self.ARGS, "--manifest", str(tmp_path / "no" / "m.json"))
        assert code == 2 and "could not write" in err
        assert queue.posts == []

    def test_refuses_a_stepped_range_because_the_queue_cannot_take_it(self) -> None:
        queue = FakeQueue()
        code, _, err = run(queue, "submit", "1-9x2", "--slices", "2", "--name", "S")
        assert code == 2 and "commands" in err
        assert queue.posts == []

    def test_a_dry_run_writes_no_manifest(self, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        run(FakeQueue(), *self.ARGS, "--manifest", str(path), "--dry-run")
        assert not path.exists()


class TestRetryAndStatus:
    def make(self, tmp_path: Path) -> tuple[FakeQueue, Path]:
        queue, path = FakeQueue(), tmp_path / "m.json"
        run(queue, "submit", "1-30", "--slices", "3", "--name", "S", "--manifest", str(path))
        return queue, path

    def test_requeues_what_died_and_leaves_the_rest(self, tmp_path: Path) -> None:
        queue, path = self.make(tmp_path)
        queue.statuses[2] = "dead"
        code, out, _ = run(queue, "retry", str(path))
        assert code == 0
        assert "requeued S_11-20 (id=2)" in out
        assert "kept S_1-10 (id=1, status=pending)" in out
        assert queue.next_id == 4

    def test_sends_what_was_never_accepted(self, tmp_path: Path) -> None:
        queue, path = FakeQueue(), tmp_path / "m.json"
        queue.refuse = {"S_11-20"}
        run(queue, "submit", "1-30", "--slices", "3", "--name", "S", "--manifest", str(path))
        queue.refuse = set()
        code, out, _ = run(queue, "retry", str(path))
        assert code == 0 and out.splitlines()[-1] == "3 of 3 jobs submitted"

    def test_reports_a_failure_while_retrying(self, tmp_path: Path) -> None:
        queue, path = self.make(tmp_path)
        queue.statuses[1] = "dead"
        queue.refuse = set()
        queue.post = lambda url, payload: (_ for _ in ()).throw(  # type: ignore[method-assign]
            QueueWatchError("could not reach the queue")
        )
        code, _, err = run(queue, "retry", str(path))
        assert code == 1 and "could not reach the queue" in err

    def test_says_when_the_manifest_cannot_be_read(self, tmp_path: Path) -> None:
        code, _, err = run(FakeQueue(), "retry", str(tmp_path / "nope.json"))
        assert code == 2 and "could not read" in err

    def test_shows_the_state_of_each_job(self, tmp_path: Path) -> None:
        queue, path = self.make(tmp_path)
        queue.statuses[3] = "running"
        code, out, _ = run(queue, "status", str(path))
        assert code == 0
        assert out.splitlines() == [
            "S_1-10  id=1  pending",
            "S_11-20  id=2  pending",
            "S_21-30  id=3  running",
        ]

    def test_status_shows_a_job_that_was_not_submitted(self, tmp_path: Path) -> None:
        queue, path = FakeQueue(), tmp_path / "m.json"
        queue.refuse = {"S_1-10"}
        run(queue, "submit", "1-10", "--slices", "1", "--name", "S", "--manifest", str(path))
        _, out, _ = run(queue, "status", str(path))
        assert out.strip() == "S_1-10  not submitted"

    def test_status_reports_a_queue_that_cannot_be_read(self, tmp_path: Path) -> None:
        code, _, err = run(FakeQueue(), "status", str(tmp_path / "nope.json"))
        assert code == 1 and "could not read" in err


class TestDependsOn:
    def test_holds_a_job_until_its_partner_succeeded(self, tmp_path: Path) -> None:
        queue, cache = FakeQueue(), tmp_path / "cache.json"
        run(queue, "submit", "1-20", "--slices", "2", "--name", "cache", "--manifest", str(cache))
        queue.statuses.update({1: "success", 2: "success"})
        code, out, _ = run(
            queue,
            *("submit", "1-20", "--slices", "2", "--name", "render", "--depends-on", str(cache)),
        )
        assert code == 0 and out.splitlines()[-1] == "2 of 2 jobs submitted"

    def test_stops_when_a_partner_died(self, tmp_path: Path) -> None:
        queue, cache = FakeQueue(), tmp_path / "cache.json"
        run(queue, "submit", "1-20", "--slices", "2", "--name", "cache", "--manifest", str(cache))
        queue.statuses.update({1: "dead", 2: "success"})
        queue.posts.clear()
        code, _, err = run(
            queue,
            *("submit", "1-20", "--slices", "2", "--name", "render", "--depends-on", str(cache)),
        )
        assert code == 1 and "dead" in err
        assert queue.posts == []

    def test_refuses_before_sending_when_the_frames_do_not_match(self, tmp_path: Path) -> None:
        queue, cache = FakeQueue(), tmp_path / "cache.json"
        run(queue, "submit", "1-20", "--slices", "2", "--name", "cache", "--manifest", str(cache))
        queue.posts.clear()
        code, _, err = run(
            queue,
            *("submit", "1-30", "--slices", "3", "--name", "render", "--depends-on", str(cache)),
        )
        assert code == 2 and "21-30" in err
        assert queue.posts == []


class TestCommands:
    def test_prints_one_command_per_slice(self) -> None:
        code, out, _ = run(
            None, "commands", "1-10", "--slices", "2", "--tool", "blender", "--scene", "s.blend"
        )
        assert code == 0
        assert out.splitlines() == [
            "blender -b s.blend -noaudio -s 1 -e 5 -a",
            "blender -b s.blend -noaudio -s 6 -e 10 -a",
        ]

    def test_quotes_what_a_shell_would_split(self) -> None:
        _, out, _ = run(
            None,
            *("commands", "1-2", "--slices", "1", "--tool", "husk"),
            *("--scene", "my shots/s.usd", "--output", "out/f.$F4.exr"),
        )
        assert out.strip() == "husk -f 1 -n 2 -i 1 -o 'out/f.$F4.exr' 'my shots/s.usd'"

    def test_asks_for_the_output_driver_of_hbatch(self) -> None:
        code, _, err = run(
            None, "commands", "1-2", "--slices", "1", "--tool", "hbatch", "--scene", "s.hip"
        )
        assert code == 2 and "--rop" in err

    def test_runs_a_stepped_range_with_the_step(self) -> None:
        _, out, _ = run(
            None, "commands", "1-9x2", "--slices", "1", "--tool", "blender", "--scene", "s.blend"
        )
        assert "-j 2" in out

    def test_lists_only_the_known_tools(self) -> None:
        code, _, err = run(
            None, "commands", "1-2", "--slices", "1", "--tool", "maya", "--scene", "s.ma"
        )
        assert code == 2 and "blender" in err


@pytest.mark.parametrize("command", ["retry", "status"])
def test_the_help_lists_the_new_commands(command: str) -> None:
    assert command in CliRunner().invoke(main, ["--help"]).output
