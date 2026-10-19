import re
from dataclasses import dataclass

MAX_SLICES = 1000

_RANGE_PATTERN = re.compile(r"^\s*(-?[0-9]+)\s*-\s*(-?[0-9]+)\s*$")


class FrameRangeError(ValueError):
    """A frame range or slice count that cannot be used."""


@dataclass(frozen=True)
class FrameRange:
    start: int
    end: int

    @property
    def count(self) -> int:
        return self.end - self.start + 1


def parse_range(text: str) -> FrameRange:
    match = _RANGE_PATTERN.match(text)
    if match is None:
        raise FrameRangeError(f"not a frame range: {text!r} (expected START-END, e.g. 1001-1240)")
    start, end = int(match.group(1)), int(match.group(2))
    if end < start:
        raise FrameRangeError(f"the range ends before it starts: {text.strip()}")
    return FrameRange(start, end)


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
    start = frame_range.start
    for index in range(slices):
        length = size + (1 if index < leftover else 0)
        result.append(FrameRange(start, start + length - 1))
        start += length
    return result


def format_range(frame_range: FrameRange) -> str:
    return f"{frame_range.start}-{frame_range.end}"


def format_slice(index: int, total: int, frame_range: FrameRange) -> str:
    unit = "frame" if frame_range.count == 1 else "frames"
    return f"slice {index}/{total}  {format_range(frame_range)}  ({frame_range.count} {unit})"
