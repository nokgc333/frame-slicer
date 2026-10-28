import re
from dataclasses import dataclass

MAX_SLICES = 1000

_RANGE_PATTERN = re.compile(r"^\s*(-?[0-9]+)\s*-\s*(-?[0-9]+)\s*(?:x\s*([0-9]+)\s*)?$")


class FrameRangeError(ValueError):
    """A frame range or slice count that cannot be used."""


@dataclass(frozen=True)
class FrameRange:
    start: int
    end: int
    step: int = 1

    @property
    def count(self) -> int:
        """How many frames are rendered (with a step, only every step-th frame)."""
        return (self.end - self.start) // self.step + 1

    def frame(self, index: int) -> int:
        """The frame number of the index-th rendered frame (0 is the first)."""
        return self.start + index * self.step

    def part(self, first: int, length: int) -> "FrameRange":
        """The slice of `length` rendered frames beginning with the first-th one."""
        return FrameRange(self.frame(first), self.frame(first + length - 1), self.step)


def parse_range(text: str) -> FrameRange:
    match = _RANGE_PATTERN.match(text)
    if match is None:
        raise FrameRangeError(f"not a frame range: {text!r} (expected START-END, e.g. 1001-1240)")
    start, end = int(match.group(1)), int(match.group(2))
    step = int(match.group(3)) if match.group(3) is not None else 1
    if end < start:
        raise FrameRangeError(f"the range ends before it starts: {text.strip()}")
    if step < 1:
        raise FrameRangeError(f"the step must be at least 1, got {step}")
    return FrameRange(start, start + (end - start) // step * step, step)


def parse_ranges(text: str) -> list[FrameRange]:
    """Read one or several comma-separated ranges; they must not overlap."""
    parts = [part.strip() for part in text.split(",")]
    if not all(parts):
        raise FrameRangeError(f"an empty part in the frame ranges: {text!r}")
    ranges = [parse_range(part) for part in parts]
    ordered = sorted(ranges, key=lambda r: r.start)
    for before, after in zip(ordered, ordered[1:]):
        if after.start <= before.end:
            raise FrameRangeError(
                f"the ranges overlap: {format_range(before)} and {format_range(after)}"
            )
    return ranges


def split_range(frame_range: FrameRange, slices: int) -> list[FrameRange]:
    if slices < 1:
        raise FrameRangeError(f"the slice count must be at least 1, got {slices}")
    if slices > MAX_SLICES:
        raise FrameRangeError(f"the slice count must be at most {MAX_SLICES}, got {slices}")
    if slices > frame_range.count:
        raise FrameRangeError(
            f"more slices ({slices}) than frames ({frame_range.count})"
            f" in {format_range(frame_range)}"
        )

    size, leftover = divmod(frame_range.count, slices)
    result: list[FrameRange] = []
    first = 0
    for index in range(slices):
        length = size + (1 if index < leftover else 0)
        result.append(frame_range.part(first, length))
        first += length
    return result


def chunk_range(frame_range: FrameRange, size: int) -> list[FrameRange]:
    """Cut the range into slices of `size` frames; the last one may be smaller."""
    if size < 1:
        raise FrameRangeError(f"the chunk size must be at least 1, got {size}")
    total = -(-frame_range.count // size)
    if total > MAX_SLICES:
        raise FrameRangeError(
            f"a chunk size of {size} would make {total} slices; the most allowed is {MAX_SLICES}"
        )
    return [
        frame_range.part(first, min(size, frame_range.count - first))
        for first in range(0, frame_range.count, size)
    ]


def plan_slices(
    ranges: list[FrameRange], slices: int | None, chunk_size: int | None
) -> list[FrameRange]:
    """Cut the ranges either into a number of slices or into fixed-size chunks (not both)."""
    if (slices is None) == (chunk_size is None):
        raise FrameRangeError("give either a slice count or a chunk size")
    if slices is not None:
        if len(ranges) > 1:
            raise FrameRangeError("several ranges can only be cut with a chunk-size")
        return split_range(ranges[0], slices)
    assert chunk_size is not None
    plan = [part for frame_range in ranges for part in chunk_range(frame_range, chunk_size)]
    if len(plan) > MAX_SLICES:
        raise FrameRangeError(
            f"the plan would make {len(plan)} slices; the most allowed is {MAX_SLICES}"
        )
    return plan


def heavy_first(slices: list[FrameRange], heavy: list[FrameRange]) -> list[FrameRange]:
    """Move the slices that hold a heavy frame to the front; the order is otherwise kept."""

    def holds_heavy(part: FrameRange) -> bool:
        return any(part.start <= h.end and h.start <= part.end for h in heavy)

    return [p for p in slices if holds_heavy(p)] + [p for p in slices if not holds_heavy(p)]


def format_range(frame_range: FrameRange) -> str:
    step = f"x{frame_range.step}" if frame_range.step > 1 else ""
    return f"{frame_range.start}-{frame_range.end}{step}"


def format_slice(index: int, total: int, frame_range: FrameRange) -> str:
    unit = "frame" if frame_range.count == 1 else "frames"
    return f"slice {index}/{total}  {format_range(frame_range)}  ({frame_range.count} {unit})"
