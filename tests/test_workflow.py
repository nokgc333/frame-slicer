from typing import Any

import pytest

from frame_slicer.errors import QueueWatchError
from frame_slicer.jobs import build_jobs
from frame_slicer.manifest import Manifest, new_manifest
from frame_slicer.ranges import FrameRange
from frame_slicer.workflow import (
    Api,
    check_dependencies,
    retry_all,
    submit_all,
    wait_for_dependency,
)

URL = "http://q"


class FakeQueue:
    """A queue that numbers jobs, remembers their status, and can refuse chosen submissions."""

    def __init__(self) -> None:
        self.statuses: dict[int, str] = {}
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[str] = []
        self.refuse: set[str] = set()
        self.next_id = 1

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((url, payload))
        if url.endswith("/requeue"):
            job_id = int(url.split("/")[-2])
            self.statuses[job_id] = "pending"
            return {"id": job_id, "status": "pending"}
        if payload["name"] in self.refuse:
            raise QueueWatchError("QueueWatch answered HTTP 503")
        job_id, self.next_id = self.next_id, self.next_id + 1
        self.statuses[job_id] = "pending"
        return {"id": job_id, "status": "pending"}

    def get(self, url: str) -> dict[str, Any]:
        self.gets.append(url)
        job_id = int(url.rsplit("/", 1)[1])
        return {"id": job_id, "status": self.statuses[job_id]}

    @property
    def api(self) -> Api:
        return Api(self.post, self.get)


def manifest(name: str = "S", count: int = 3) -> Manifest:
    slices = [FrameRange(i * 10 + 1, i * 10 + 10) for i in range(count)]
    return new_manifest(URL, build_jobs(name, slices, "normal"))


class Saves:
    def __init__(self) -> None:
        self.saved: list[Manifest] = []

    def __call__(self, m: Manifest) -> None:
        self.saved.append(m)


class TestSubmitAll:
    def test_submits_every_job_and_records_the_answers(self) -> None:
        queue, saves, lines = FakeQueue(), Saves(), []
        done, error = submit_all(queue.api, manifest(), saves, lines.append)
        assert error is None
        assert [r.id for r in done.jobs] == [1, 2, 3]
        assert [r.status for r in done.jobs] == ["pending"] * 3
        assert lines[0] == "submitted S_1-10 (id=1, status=pending)"

    def test_saves_after_every_job_so_a_crash_loses_nothing(self) -> None:
        saves = Saves()
        submit_all(FakeQueue().api, manifest(), saves, lambda _: None)
        assert [sum(r.id is not None for r in m.jobs) for m in saves.saved] == [1, 2, 3]

    def test_stops_at_the_first_failure_and_records_why(self) -> None:
        queue, saves = FakeQueue(), Saves()
        queue.refuse = {"S_11-20"}
        done, error = submit_all(queue.api, manifest(), saves, lambda _: None)
        assert isinstance(error, QueueWatchError)
        assert [r.id for r in done.jobs] == [1, None, None]
        assert done.jobs[1].error is not None and "503" in done.jobs[1].error
        assert saves.saved[-1] == done

    def test_does_not_send_a_job_that_already_has_an_id(self) -> None:
        queue = FakeQueue()
        first, _ = submit_all(queue.api, manifest(), Saves(), lambda _: None)
        queue.posts.clear()
        submit_all(queue.api, first, Saves(), lambda _: None)
        assert queue.posts == []

    def test_waits_for_the_dependency_before_each_job(self) -> None:
        queue, order = FakeQueue(), []
        wait = lambda record: order.append(("wait", record.name))  # noqa: E731
        original_post = queue.post

        def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            order.append(("post", payload["name"]))
            return original_post(url, payload)

        submit_all(Api(post, queue.get), manifest(count=2), Saves(), lambda _: None, wait)
        assert order == [
            ("wait", "S_1-10"),
            ("post", "S_1-10"),
            ("wait", "S_11-20"),
            ("post", "S_11-20"),
        ]

    def test_a_failed_wait_stops_the_submission_and_is_recorded(self) -> None:
        def wait(record: Any) -> None:
            raise QueueWatchError("the cache job died")

        done, error = submit_all(FakeQueue().api, manifest(), Saves(), lambda _: None, wait)
        assert error is not None and "died" in str(error)
        assert done.jobs[0].error == "the cache job died"


class TestRetryAll:
    def test_sends_the_jobs_that_were_never_accepted(self) -> None:
        queue = FakeQueue()
        queue.refuse = {"S_11-20"}
        first, _ = submit_all(queue.api, manifest(), Saves(), lambda _: None)
        queue.refuse = set()
        queue.posts.clear()
        done, error = retry_all(queue.api, first, Saves(), lambda _: None)
        assert error is None
        assert [p[1]["name"] for p in queue.posts] == ["S_11-20", "S_21-30"]
        assert all(r.id is not None for r in done.jobs)

    def test_requeues_only_the_jobs_that_died_or_were_cancelled(self) -> None:
        queue = FakeQueue()
        first, _ = submit_all(queue.api, manifest(count=4), Saves(), lambda _: None)
        queue.statuses.update({1: "success", 2: "dead", 3: "running", 4: "cancelled"})
        queue.posts.clear()
        done, _ = retry_all(queue.api, first, Saves(), lambda _: None)
        assert [p[0] for p in queue.posts] == [f"{URL}/jobs/2/requeue", f"{URL}/jobs/4/requeue"]
        assert [r.status for r in done.jobs] == ["success", "pending", "running", "pending"]

    def test_does_not_create_new_jobs_for_the_ones_it_requeues(self) -> None:
        queue = FakeQueue()
        first, _ = submit_all(queue.api, manifest(count=1), Saves(), lambda _: None)
        queue.statuses[1] = "dead"
        retry_all(queue.api, first, Saves(), lambda _: None)
        assert queue.next_id == 2

    def test_says_what_it_did_for_each_job(self) -> None:
        queue, lines = FakeQueue(), []
        first, _ = submit_all(queue.api, manifest(count=2), Saves(), lambda _: None)
        queue.statuses[2] = "dead"
        retry_all(queue.api, first, Saves(), lines.append)
        assert lines == ["kept S_1-10 (id=1, status=pending)", "requeued S_11-20 (id=2)"]

    def test_stops_at_a_failure_and_keeps_what_it_learned(self) -> None:
        queue = FakeQueue()
        first, _ = submit_all(queue.api, manifest(count=2), Saves(), lambda _: None)
        queue.statuses[1] = "dead"

        def post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise QueueWatchError("could not reach the queue")

        done, error = retry_all(Api(post, queue.get), first, Saves(), lambda _: None)
        assert error is not None
        assert done.jobs[0].status == "dead"
        assert done.jobs[0].error == "could not reach the queue"


class TestDependencies:
    def test_every_job_needs_a_partner_with_the_same_frames(self) -> None:
        cache = manifest("cache", 3)
        render = manifest("render", 3)
        check_dependencies(cache, render)
        with pytest.raises(QueueWatchError, match="21-30"):
            check_dependencies(manifest("cache", 2), render)

    def test_waits_until_the_partner_has_succeeded(self) -> None:
        queue = FakeQueue()
        cache, _ = submit_all(queue.api, manifest("cache", 1), Saves(), lambda _: None)
        states = iter(["pending", "running", "success"])
        sleeps: list[float] = []

        def get(url: str) -> dict[str, Any]:
            return {"status": next(states)}

        wait = wait_for_dependency(
            Api(queue.post, get), cache, 100, 1.0, sleep=sleeps.append, clock=lambda: 0.0
        )
        wait(manifest("render", 1).jobs[0])
        assert sleeps == [1.0, 1.0]

    @pytest.mark.parametrize("status", ["dead", "cancelled"])
    def test_stops_when_the_partner_cannot_succeed(self, status: str) -> None:
        queue = FakeQueue()
        cache, _ = submit_all(queue.api, manifest("cache", 1), Saves(), lambda _: None)
        queue.statuses[1] = status
        wait = wait_for_dependency(queue.api, cache, 100, 1.0, sleep=lambda _: None)
        with pytest.raises(QueueWatchError, match=status):
            wait(manifest("render", 1).jobs[0])

    def test_gives_up_after_the_time_limit(self) -> None:
        queue = FakeQueue()
        cache, _ = submit_all(queue.api, manifest("cache", 1), Saves(), lambda _: None)
        ticks = iter([0.0, 5.0, 11.0, 17.0])
        wait = wait_for_dependency(
            queue.api, cache, 10, 1.0, sleep=lambda _: None, clock=lambda: next(ticks)
        )
        with pytest.raises(QueueWatchError, match="10 seconds"):
            wait(manifest("render", 1).jobs[0])

    def test_stops_when_the_partner_was_never_submitted(self) -> None:
        wait = wait_for_dependency(
            FakeQueue().api, manifest("cache", 1), 100, 1.0, sleep=lambda _: None
        )
        with pytest.raises(QueueWatchError, match="never submitted"):
            wait(manifest("render", 1).jobs[0])


class TestUnexpectedAnswers:
    @pytest.mark.parametrize("answer", [{}, {"id": True, "status": "x"}, {"id": 1, "status": 2}])
    def test_submit_rejects_an_answer_without_a_usable_id_and_status(
        self, answer: dict[str, Any]
    ) -> None:
        api = Api(lambda url, payload: answer, lambda url: {})
        done, error = submit_all(api, manifest(count=1), Saves(), lambda _: None)
        assert error is not None and "unexpected answer" in str(error)
        assert done.jobs[0].id is None

    def test_retry_rejects_a_status_that_is_not_text(self) -> None:
        queue = FakeQueue()
        first, _ = submit_all(queue.api, manifest(count=1), Saves(), lambda _: None)
        api = Api(queue.post, lambda url: {"status": 5})
        _, error = retry_all(api, first, Saves(), lambda _: None)
        assert error is not None and "unexpected answer" in str(error)

    def test_requeue_rejects_an_answer_without_a_status(self) -> None:
        queue = FakeQueue()
        first, _ = submit_all(queue.api, manifest(count=1), Saves(), lambda _: None)
        queue.statuses[1] = "dead"
        api = Api(lambda url, payload: {}, queue.get)
        _, error = retry_all(api, first, Saves(), lambda _: None)
        assert error is not None and "unexpected answer" in str(error)

    def test_a_dependency_without_a_matching_job_is_refused_when_waiting(self) -> None:
        wait = wait_for_dependency(FakeQueue().api, manifest("cache", 1), 1, 1.0)
        with pytest.raises(QueueWatchError, match="no dependency job"):
            wait(manifest("render", 2).jobs[1])
