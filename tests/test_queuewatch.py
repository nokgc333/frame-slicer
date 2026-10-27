from typing import Any

import pytest

from frame_slicer.errors import QueueWatchError
from frame_slicer.jobs import JobRequest
from frame_slicer.queuewatch import submit_jobs


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
