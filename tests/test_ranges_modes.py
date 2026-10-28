"""Stepped ranges, several ranges, fixed-size chunks and heavy-frames-first ordering."""

import pytest

from frame_slicer.ranges import (
    MAX_SLICES,
    FrameRange,
    FrameRangeError,
    chunk_range,
    format_range,
    heavy_first,
    parse_range,
    parse_ranges,
    plan_slices,
    split_range,
)


class TestStep:
    def test_reads_a_step(self) -> None:
        assert parse_range("1-100x2") == FrameRange(1, 99, 2)

    def test_pulls_the_end_back_to_the_last_frame_that_is_rendered(self) -> None:
        assert parse_range("1-10x4") == FrameRange(1, 9, 4)

    def test_counts_only_the_frames_that_are_rendered(self) -> None:
        assert FrameRange(1, 99, 2).count == 50

    def test_a_step_of_one_is_the_plain_form(self) -> None:
        assert parse_range("1-10x1") == FrameRange(1, 10)

    @pytest.mark.parametrize("text", ["1-10x0", "1-10x-2", "1-10x", "1-10x2x2", "1-10 x 2.5"])
    def test_rejects_a_bad_step(self, text: str) -> None:
        with pytest.raises(FrameRangeError):
            parse_range(text)

    def test_shows_the_step_in_the_name_only_when_there_is_one(self) -> None:
        assert format_range(FrameRange(1, 99, 2)) == "1-99x2"
        assert format_range(FrameRange(1, 10)) == "1-10"

    def test_splits_a_stepped_range_by_the_frames_that_are_rendered(self) -> None:
        parts = split_range(FrameRange(1, 99, 2), 5)
        assert parts == [
            FrameRange(1, 19, 2),
            FrameRange(21, 39, 2),
            FrameRange(41, 59, 2),
            FrameRange(61, 79, 2),
            FrameRange(81, 99, 2),
        ]

    def test_counts_more_slices_than_rendered_frames_as_an_error(self) -> None:
        with pytest.raises(FrameRangeError, match="more slices"):
            split_range(FrameRange(1, 9, 4), 4)


class TestSeveralRanges:
    def test_reads_ranges_separated_by_commas(self) -> None:
        assert parse_ranges("1-10, 50-60x2") == [FrameRange(1, 10), FrameRange(50, 60, 2)]

    def test_a_single_range_is_a_list_of_one(self) -> None:
        assert parse_ranges("1-10") == [FrameRange(1, 10)]

    @pytest.mark.parametrize("text", ["", "1-10,", ",1-10", "1-10,,20-30"])
    def test_rejects_an_empty_part(self, text: str) -> None:
        with pytest.raises(FrameRangeError):
            parse_ranges(text)

    def test_rejects_ranges_that_overlap(self) -> None:
        with pytest.raises(FrameRangeError, match="overlap"):
            parse_ranges("1-10,10-20")

    def test_keeps_the_order_it_was_given(self) -> None:
        assert parse_ranges("50-60,1-10") == [FrameRange(50, 60), FrameRange(1, 10)]


class TestChunk:
    def test_cuts_fixed_size_chunks_and_a_smaller_last_one(self) -> None:
        assert chunk_range(FrameRange(1, 10), 4) == [
            FrameRange(1, 4),
            FrameRange(5, 8),
            FrameRange(9, 10),
        ]

    def test_a_chunk_as_big_as_the_range_is_one_slice(self) -> None:
        assert chunk_range(FrameRange(1, 10), 10) == [FrameRange(1, 10)]

    def test_chunks_a_stepped_range_by_rendered_frames(self) -> None:
        assert chunk_range(FrameRange(1, 9, 2), 2) == [
            FrameRange(1, 3, 2),
            FrameRange(5, 7, 2),
            FrameRange(9, 9, 2),
        ]

    def test_rejects_a_size_below_one(self) -> None:
        with pytest.raises(FrameRangeError, match="at least 1"):
            chunk_range(FrameRange(1, 10), 0)

    def test_rejects_a_size_that_would_make_too_many_slices(self) -> None:
        with pytest.raises(FrameRangeError, match=str(MAX_SLICES)):
            chunk_range(FrameRange(1, MAX_SLICES * 2), 1)


class TestPlanSlices:
    def test_splits_into_a_number_of_slices(self) -> None:
        assert len(plan_slices([FrameRange(1, 10)], slices=5, chunk_size=None)) == 5

    def test_cuts_every_range_into_chunks(self) -> None:
        plan = plan_slices([FrameRange(1, 5), FrameRange(11, 13)], slices=None, chunk_size=2)
        assert plan == [
            FrameRange(1, 2),
            FrameRange(3, 4),
            FrameRange(5, 5),
            FrameRange(11, 12),
            FrameRange(13, 13),
        ]

    def test_needs_exactly_one_way_to_cut(self) -> None:
        with pytest.raises(FrameRangeError, match="either"):
            plan_slices([FrameRange(1, 10)], slices=None, chunk_size=None)
        with pytest.raises(FrameRangeError, match="either"):
            plan_slices([FrameRange(1, 10)], slices=2, chunk_size=2)

    def test_a_slice_count_does_not_fit_several_ranges(self) -> None:
        with pytest.raises(FrameRangeError, match="chunk-size"):
            plan_slices([FrameRange(1, 10), FrameRange(20, 30)], slices=2, chunk_size=None)

    def test_rejects_a_plan_with_too_many_slices(self) -> None:
        ranges = [FrameRange(i * 10, i * 10 + 5) for i in range(MAX_SLICES + 1)]
        with pytest.raises(FrameRangeError, match=str(MAX_SLICES)):
            plan_slices(ranges, slices=None, chunk_size=6)


class TestHeavyFirst:
    def test_puts_the_slices_that_hold_a_heavy_frame_first(self) -> None:
        slices = [FrameRange(1, 10), FrameRange(11, 20), FrameRange(21, 30)]
        assert heavy_first(slices, [FrameRange(25, 25)]) == [
            FrameRange(21, 30),
            FrameRange(1, 10),
            FrameRange(11, 20),
        ]

    def test_keeps_the_original_order_inside_each_group(self) -> None:
        slices = [FrameRange(1, 10), FrameRange(11, 20), FrameRange(21, 30), FrameRange(31, 40)]
        heavy = [FrameRange(35, 36), FrameRange(15, 16)]
        assert heavy_first(slices, heavy) == [
            FrameRange(11, 20),
            FrameRange(31, 40),
            FrameRange(1, 10),
            FrameRange(21, 30),
        ]

    def test_changes_nothing_without_heavy_frames(self) -> None:
        slices = [FrameRange(1, 10), FrameRange(11, 20)]
        assert heavy_first(slices, []) == slices
