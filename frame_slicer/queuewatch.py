import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from frame_slicer.ranges import FrameRange, format_range

DEFAULT_API_BASE_URL = "http://localhost:8000"
VALID_PRIORITIES = ("high", "normal", "low")
MAX_NAME_LENGTH = 100
TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 1_000_000

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

Post = Callable[[str, dict[str, Any]], dict[str, Any]]


class QueueWatchError(Exception):
    """A job that cannot be built, or a queue that did not take it."""


@dataclass(frozen=True)
class JobRequest:
    name: str
    priority: str


@dataclass(frozen=True)
class SubmittedJob:
    name: str
    id: int
    status: str


def build_jobs(base_name: str, slices: Sequence[FrameRange], priority: str) -> list[JobRequest]:
    if _CONTROL_CHARACTERS.search(base_name):
        raise QueueWatchError("the job name must not contain control characters")
    name = base_name.strip()
    if not name:
        raise QueueWatchError("the job name must not be empty")
    if priority not in VALID_PRIORITIES:
        raise QueueWatchError(f"the priority must be one of {', '.join(VALID_PRIORITIES)}")

    jobs = [JobRequest(f"{name}_{format_range(part)}", priority) for part in slices]
    longest = max((len(job.name) for job in jobs), default=0)
    if longest > MAX_NAME_LENGTH:
        raise QueueWatchError(
            f"a job name would be {longest} characters; the queue accepts at most {MAX_NAME_LENGTH}"
        )
    return jobs


def normalize_base_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise QueueWatchError(f"the QueueWatch address must be an http(s) URL, got {url!r}")
    return url.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def http_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as error:
        raise QueueWatchError(f"QueueWatch answered HTTP {error.code} for {url}") from error
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise QueueWatchError(f"could not reach {url}: {reason}") from error

    try:
        answer = json.loads(body)
    except ValueError as error:
        raise QueueWatchError(f"the answer from {url} was not JSON") from error
    if not isinstance(answer, dict):
        raise QueueWatchError(f"the answer from {url} was not a JSON object")
    return answer


def submit_jobs(
    api_base_url: str, jobs: Sequence[JobRequest], post: Post = http_post
) -> Iterator[SubmittedJob]:
    url = f"{normalize_base_url(api_base_url)}/jobs"
    for job in jobs:
        answer = post(url, {"name": job.name, "priority": job.priority})
        job_id, status = answer.get("id"), answer.get("status")
        if not isinstance(job_id, int) or isinstance(job_id, bool) or not isinstance(status, str):
            raise QueueWatchError(f"unexpected answer for {job.name!r}: {answer!r}")
        yield SubmittedJob(job.name, job_id, status)
