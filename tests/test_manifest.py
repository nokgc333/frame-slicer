import json
from pathlib import Path

import pytest

from frame_slicer.errors import QueueWatchError
from frame_slicer.jobs import build_jobs
from frame_slicer.manifest import (
    JobRecord,
    Manifest,
    load_manifest,
    new_manifest,
    save_manifest,
)
from frame_slicer.ranges import FrameRange


def sample() -> Manifest:
    jobs = build_jobs("S", [FrameRange(1, 5), FrameRange(6, 9)], "high", scene="a.blend")
    return new_manifest("http://localhost:8000", jobs)


class TestNewManifest:
    def test_records_every_job_as_not_yet_submitted(self) -> None:
        manifest = sample()
        assert [(r.name, r.start, r.end, r.id, r.status) for r in manifest.jobs] == [
            ("S_1-5", 1, 5, None, None),
            ("S_6-9", 6, 9, None, None),
        ]
        assert manifest.priority == "high"
        assert manifest.scene == "a.blend"
        assert manifest.api_base_url == "http://localhost:8000"

    def test_marks_a_record_as_submitted_without_changing_the_original(self) -> None:
        manifest = sample()
        updated = manifest.with_record(0, manifest.jobs[0].submitted(7, "pending"))
        assert updated.jobs[0].id == 7 and updated.jobs[0].status == "pending"
        assert manifest.jobs[0].id is None
        assert updated.jobs[1] == manifest.jobs[1]

    def test_marks_a_record_as_failed_with_the_reason(self) -> None:
        record = sample().jobs[0].failed("HTTP 503")
        assert record.error == "HTTP 503" and record.id is None


class TestRecord:
    def test_turns_back_into_a_job_request(self) -> None:
        manifest = sample()
        job = manifest.job_request(manifest.jobs[1])
        assert job.payload() == {
            "name": "S_6-9",
            "priority": "high",
            "frame_start": 6,
            "frame_end": 9,
            "scene": "a.blend",
        }

    def test_finds_a_record_by_its_frame_range(self) -> None:
        manifest = sample()
        assert manifest.find(FrameRange(6, 9)) == 1
        assert manifest.find(FrameRange(6, 8)) is None


class TestSaveAndLoad:
    def test_round_trips(self, tmp_path: Path) -> None:
        manifest = sample().with_record(0, sample().jobs[0].submitted(3, "success"))
        path = tmp_path / "m.json"
        save_manifest(path, manifest)
        assert load_manifest(path) == manifest

    def test_writes_json_a_person_can_read(self, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        save_manifest(path, sample())
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["jobs"][0]["name"] == "S_1-5"
        assert "token" not in path.read_text(encoding="utf-8").lower()

    def test_leaves_no_temporary_file_behind(self, tmp_path: Path) -> None:
        save_manifest(tmp_path / "m.json", sample())
        assert [p.name for p in tmp_path.iterdir()] == ["m.json"]

    def test_replaces_a_file_without_losing_it_if_the_write_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "m.json"
        save_manifest(path, sample())
        before = path.read_text(encoding="utf-8")

        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("frame_slicer.manifest.os.replace", fail)
        with pytest.raises(QueueWatchError, match="could not write"):
            save_manifest(path, sample().with_record(0, sample().jobs[0].failed("x")))
        assert path.read_text(encoding="utf-8") == before
        assert [p.name for p in tmp_path.iterdir()] == ["m.json"]

    def test_reports_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(QueueWatchError, match="could not read"):
            load_manifest(tmp_path / "nope.json")

    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            "[]",
            '{"version": 2}',
            '{"version": 1}',
            '{"version": 1, "api_base_url": "http://h", "name": "S", "priority": "high",'
            ' "scene": null, "jobs": "x"}',
            '{"version": 1, "api_base_url": "http://h", "name": "S", "priority": "high",'
            ' "scene": null, "jobs": [{"name": "a"}]}',
        ],
    )
    def test_refuses_a_file_that_is_not_a_manifest(self, tmp_path: Path, text: str) -> None:
        path = tmp_path / "m.json"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(QueueWatchError, match="manifest"):
            load_manifest(path)


class TestJobRecord:
    def test_a_record_knows_its_range(self) -> None:
        assert JobRecord("a", 1, 2, 1, None, None, None).frames == FrameRange(1, 2)
