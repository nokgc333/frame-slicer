import re
from collections.abc import Sequence
from dataclasses import dataclass

from frame_slicer.errors import QueueWatchError
from frame_slicer.ranges import FrameRange, format_range

VALID_PRIORITIES = ("high", "normal", "low")
MAX_NAME_LENGTH = 100

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class JobRequest:
    name: str
    priority: str


def clean_base_name(base_name: str) -> str:
    """Return the name without surrounding spaces; refuse control characters and an empty name."""
    if _CONTROL_CHARACTERS.search(base_name):
        raise QueueWatchError("the job name must not contain control characters")
    name = base_name.strip()
    if not name:
        raise QueueWatchError("the job name must not be empty")
    return name


def check_priority(priority: str) -> None:
    """Refuse a priority the queue does not know."""
    if priority not in VALID_PRIORITIES:
        raise QueueWatchError(f"the priority must be one of {', '.join(VALID_PRIORITIES)}")


def build_jobs(base_name: str, slices: Sequence[FrameRange], priority: str) -> list[JobRequest]:
    """Name one job per slice as NAME_START-END, checking the name and priority first.

    The queue keeps only a name and a priority, so the frame range travels in the name.
    """
    name = clean_base_name(base_name)
    check_priority(priority)

    jobs = [JobRequest(f"{name}_{format_range(part)}", priority) for part in slices]
    longest = max((len(job.name) for job in jobs), default=0)
    if longest > MAX_NAME_LENGTH:
        raise QueueWatchError(
            f"a job name would be {longest} characters; the queue accepts at most {MAX_NAME_LENGTH}"
        )
    return jobs
