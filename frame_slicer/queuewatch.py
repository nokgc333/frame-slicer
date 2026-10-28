from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from frame_slicer.errors import QueueWatchError
from frame_slicer.jobs import JobRequest
from frame_slicer.transport import Post, http_post, normalize_base_url

DEFAULT_API_BASE_URL = "http://localhost:8000"


@dataclass(frozen=True)
class SubmittedJob:
    name: str
    id: int
    status: str


def _read_answer(job: JobRequest, answer: dict[str, Any]) -> SubmittedJob:
    """Turn the queue's answer into a SubmittedJob, or fail if its id or status is not usable."""
    job_id, status = answer.get("id"), answer.get("status")
    if not isinstance(job_id, int) or isinstance(job_id, bool) or not isinstance(status, str):
        raise QueueWatchError(f"unexpected answer for {job.name!r}: {answer!r}")
    return SubmittedJob(job.name, job_id, status)


def submit_jobs(
    api_base_url: str, jobs: Sequence[JobRequest], post: Post = http_post
) -> Iterator[SubmittedJob]:
    """Post each job to the queue in order, yielding it as soon as the queue accepts it.

    Stops at the first failure: the jobs already accepted are not taken back.
    """
    url = f"{normalize_base_url(api_base_url)}/jobs"
    for job in jobs:
        yield _read_answer(job, post(url, {"name": job.name, "priority": job.priority}))
