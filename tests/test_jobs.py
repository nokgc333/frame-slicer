import pytest

from frame_slicer.errors import QueueWatchError
from frame_slicer.jobs import MAX_NAME_LENGTH, JobRequest, build_jobs
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
