import pytest

from frame_slicer.ranges import (
    MAX_SLICES,
    FrameRange,
    FrameRangeError,
    format_slice,
    parse_range,
    split_range,
)


class TestParseRange:
    def test_reads_start_and_end_when_given_a_plain_range(self) -> None:
        assert parse_range("1001-1240") == FrameRange(1001, 1240)

    def test_accepts_a_single_frame(self) -> None:
        assert parse_range("1001-1001") == FrameRange(1001, 1001)

    def test_ignores_spaces_around_the_numbers(self) -> None:
        assert parse_range(" 1001 - 1240 ") == FrameRange(1001, 1240)

    def test_accepts_negative_frames(self) -> None:
        assert parse_range("-10-5") == FrameRange(-10, 5)
        assert parse_range("-10--5") == FrameRange(-10, -5)

    @pytest.mark.parametrize(
        "text",
        ["", "1001", "1001-", "-1240", "a-b", "1001..1240", "1001-1240-1300", "1.5-3", "1001 1240"],
    )
    def test_rejects_text_that_is_not_a_range(self, text: str) -> None:
        with pytest.raises(FrameRangeError, match="START-END"):
            parse_range(text)

    def test_rejects_a_range_that_ends_before_it_starts(self) -> None:
        with pytest.raises(FrameRangeError, match="before"):
            parse_range("1240-1001")

    def test_names_the_given_text_in_the_error(self) -> None:
        with pytest.raises(FrameRangeError, match="'abc'"):
            parse_range("abc")


class TestFrameRange:
    def test_counts_both_ends(self) -> None:
        assert FrameRange(1001, 1240).count == 240
        assert FrameRange(5, 5).count == 1
        assert FrameRange(-2, 2).count == 5


class TestSplitRange:
    def test_cuts_an_even_range_into_equal_slices(self) -> None:
        slices = split_range(FrameRange(1001, 1240), 8)
        assert len(slices) == 8
        assert slices[0] == FrameRange(1001, 1030)
        assert slices[1] == FrameRange(1031, 1060)
        assert slices[7] == FrameRange(1211, 1240)

    def test_gives_the_leftover_frames_to_the_first_slices(self) -> None:
        slices = split_range(FrameRange(1, 10), 3)
        assert [s.count for s in slices] == [4, 3, 3]
        assert slices == [FrameRange(1, 4), FrameRange(5, 7), FrameRange(8, 10)]

    def test_makes_slice_sizes_differ_by_at_most_one(self) -> None:
        for total in range(1, 60):
            for count in range(1, total + 1):
                sizes = [s.count for s in split_range(FrameRange(1, total), count)]
                assert max(sizes) - min(sizes) <= 1

    def test_covers_every_frame_once_without_gaps_or_overlaps(self) -> None:
        frame_range = FrameRange(-7, 94)
        for count in (1, 2, 5, 7, 101):
            slices = split_range(frame_range, count)
            assert slices[0].start == frame_range.start
            assert slices[-1].end == frame_range.end
            for before, after in zip(slices, slices[1:]):
                assert after.start == before.end + 1

    def test_returns_the_whole_range_for_one_slice(self) -> None:
        assert split_range(FrameRange(1001, 1240), 1) == [FrameRange(1001, 1240)]

    def test_returns_one_frame_slices_when_slices_equal_frames(self) -> None:
        slices = split_range(FrameRange(1, 3), 3)
        assert slices == [FrameRange(1, 1), FrameRange(2, 2), FrameRange(3, 3)]

    def test_rejects_more_slices_than_frames(self) -> None:
        with pytest.raises(FrameRangeError, match="more slices"):
            split_range(FrameRange(1, 3), 4)

    @pytest.mark.parametrize("count", [0, -1])
    def test_rejects_a_slice_count_below_one(self, count: int) -> None:
        with pytest.raises(FrameRangeError, match="at least 1"):
            split_range(FrameRange(1, 10), count)

    def test_rejects_a_slice_count_above_the_limit(self) -> None:
        with pytest.raises(FrameRangeError, match=str(MAX_SLICES)):
            split_range(FrameRange(1, 10 * MAX_SLICES), MAX_SLICES + 1)

    def test_accepts_exactly_the_limit(self) -> None:
        assert len(split_range(FrameRange(1, 10 * MAX_SLICES), MAX_SLICES)) == MAX_SLICES


class TestFormatSlice:
    def test_matches_the_documented_line(self) -> None:
        assert format_slice(1, 8, FrameRange(1001, 1030)) == "slice 1/8  1001-1030  (30 frames)"

    def test_uses_the_singular_for_one_frame(self) -> None:
        assert format_slice(3, 3, FrameRange(7, 7)) == "slice 3/3  7-7  (1 frame)"

    def test_keeps_negative_frames_readable(self) -> None:
        assert format_slice(1, 2, FrameRange(-5, -1)) == "slice 1/2  -5--1  (5 frames)"
