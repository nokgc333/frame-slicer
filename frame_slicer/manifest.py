import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from frame_slicer.errors import QueueWatchError
from frame_slicer.jobs import JobRequest
from frame_slicer.ranges import FrameRange

MANIFEST_VERSION = 1


@dataclass(frozen=True)
class JobRecord:
    """One slice: what was asked for, and what the queue answered (nothing yet, if id is None)."""

    name: str
    start: int
    end: int
    step: int
    id: int | None
    status: str | None
    error: str | None

    @property
    def frames(self) -> FrameRange:
        return FrameRange(self.start, self.end, self.step)

    def submitted(self, job_id: int, status: str) -> "JobRecord":
        return replace(self, id=job_id, status=status, error=None)

    def failed(self, error: str) -> "JobRecord":
        return replace(self, error=error)

    def with_status(self, status: str) -> "JobRecord":
        return replace(self, status=status, error=None)


@dataclass(frozen=True)
class Manifest:
    """The record of one submission: enough to retry or to wait for it later."""

    api_base_url: str
    name: str
    priority: str
    scene: str | None
    jobs: tuple[JobRecord, ...]

    def with_record(self, index: int, record: JobRecord) -> "Manifest":
        jobs = list(self.jobs)
        jobs[index] = record
        return replace(self, jobs=tuple(jobs))

    def find(self, frames: FrameRange) -> int | None:
        """The index of the record with exactly these frames, or None."""
        for index, record in enumerate(self.jobs):
            if record.frames == frames:
                return index
        return None

    def job_request(self, record: JobRecord) -> JobRequest:
        return JobRequest(record.name, self.priority, record.frames, self.scene)


def new_manifest(api_base_url: str, jobs: Sequence[JobRequest]) -> Manifest:
    """A manifest for jobs that have not been sent yet (they all come from one build_jobs)."""
    first = jobs[0]
    records = tuple(_record_for(job) for job in jobs)
    return Manifest(api_base_url, _base_name(first), first.priority, first.scene, records)


def _record_for(job: JobRequest) -> JobRecord:
    frames = job.frames
    assert frames is not None
    return JobRecord(job.name, frames.start, frames.end, frames.step, None, None, None)


def _base_name(job: JobRequest) -> str:
    assert job.frames is not None
    suffix = f"_{job.frames.start}-{job.frames.end}"
    return job.name.removesuffix(suffix)


def save_manifest(path: Path, manifest: Manifest) -> None:
    """Write the manifest as JSON, replacing any old file only once the new one is complete."""
    data: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "api_base_url": manifest.api_base_url,
        "name": manifest.name,
        "priority": manifest.priority,
        "scene": manifest.scene,
        "jobs": [record.__dict__ for record in manifest.jobs],
    }
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    temporary = None
    try:
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except OSError as error:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
        raise QueueWatchError(f"could not write the manifest {path}: {error}") from error


def load_manifest(path: Path) -> Manifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise QueueWatchError(f"could not read the manifest {path}: {error}") from error
    try:
        if data["version"] != MANIFEST_VERSION:
            raise ValueError(f"unknown version {data['version']!r}")
        jobs = tuple(JobRecord(**record) for record in data["jobs"])
        return Manifest(data["api_base_url"], data["name"], data["priority"], data["scene"], jobs)
    except (KeyError, TypeError, ValueError) as error:
        raise QueueWatchError(f"{path} is not a frame-slicer manifest: {error!r}") from error
