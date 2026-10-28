import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from frame_slicer.errors import QueueWatchError
from frame_slicer.ranges import FrameRange, format_range

VALID_PRIORITIES = ("high", "normal", "low")
MAX_NAME_LENGTH = 100

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


MAX_SCENE_LENGTH = 255


@dataclass(frozen=True)
class JobRequest:
    name: str
    priority: str
    frames: FrameRange | None = None
    scene: str | None = None

    def payload(self) -> dict[str, Any]:
        """The body the queue's POST /jobs takes; the range and scene only when they are known."""
        body: dict[str, Any] = {"name": self.name, "priority": self.priority}
        if self.frames is not None:
            body["frame_start"] = self.frames.start
            body["frame_end"] = self.frames.end
        if self.scene is not None:
            body["scene"] = self.scene
        return body


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


def check_scene(scene: str | None) -> str | None:
    """Return the scene path as the queue will read it, or refuse one it would reject."""
    if scene is None:
        return None
    if _CONTROL_CHARACTERS.search(scene):
        raise QueueWatchError("the scene path must not contain control characters")
    cleaned = scene.strip()
    if not cleaned or len(cleaned) > MAX_SCENE_LENGTH:
        raise QueueWatchError(f"the scene path must be 1 to {MAX_SCENE_LENGTH} characters")
    return cleaned


def build_jobs(
    base_name: str, slices: Sequence[FrameRange], priority: str, scene: str | None = None
) -> list[JobRequest]:
    """Name one job per slice as NAME_START-END, checking the name and priority first.

    The queue keeps only a name and a priority, so the frame range travels in the name.
    """
    name = clean_base_name(base_name)
    check_priority(priority)
    scene = check_scene(scene)
    if any(part.step > 1 for part in slices):
        raise QueueWatchError(
            "the queue renders whole frame ranges, so it cannot take a step; "
            "use the commands command to render every n-th frame"
        )

    jobs = [JobRequest(f"{name}_{format_range(part)}", priority, part, scene) for part in slices]
    longest = max((len(job.name) for job in jobs), default=0)
    if longest > MAX_NAME_LENGTH:
        raise QueueWatchError(
            f"a job name would be {longest} characters; the queue accepts at most {MAX_NAME_LENGTH}"
        )
    return jobs
