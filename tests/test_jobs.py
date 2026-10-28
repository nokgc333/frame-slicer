import pytest

from frame_slicer.errors import QueueWatchError
from frame_slicer.jobs import MAX_NAME_LENGTH, JobRequest, build_jobs
from frame_slicer.ranges import FrameRange


class TestBuildJobs:
    def test_names_each_job_after_its_frame_range(self) -> None:
        jobs = build_jobs("SHOT010_cache", [FrameRange(1001, 1030), FrameRange(1031, 1060)], "high")
        assert jobs == [
            JobRequest("SHOT010_cache_1001-1030", "high", FrameRange(1001, 1030)),
            JobRequest("SHOT010_cache_1031-1060", "high", FrameRange(1031, 1060)),
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


class TestSceneAndPayload:
    def test_carries_the_frame_range_in_the_payload(self) -> None:
        job = build_jobs("A", [FrameRange(5, 9)], "low")[0]
        assert job.payload() == {
            "name": "A_5-9",
            "priority": "low",
            "frame_start": 5,
            "frame_end": 9,
        }

    def test_adds_the_scene_when_one_is_given(self) -> None:
        job = build_jobs("A", [FrameRange(5, 9)], "low", scene="shots/s010.blend")[0]
        assert job.payload()["scene"] == "shots/s010.blend"

    def test_a_job_without_a_range_sends_only_name_and_priority(self) -> None:
        assert JobRequest("A", "low").payload() == {"name": "A", "priority": "low"}

    @pytest.mark.parametrize("scene", ["", "  ", "a\nb", "x" * 256])
    def test_rejects_a_scene_the_queue_would_refuse(self, scene: str) -> None:
        with pytest.raises(QueueWatchError, match="scene"):
            build_jobs("A", [FrameRange(1, 2)], "normal", scene=scene)

    def test_refuses_a_stepped_slice_because_the_queue_renders_whole_ranges(self) -> None:
        with pytest.raises(QueueWatchError, match="step"):
            build_jobs("A", [FrameRange(1, 9, 2)], "normal")
