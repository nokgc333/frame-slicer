from click.testing import CliRunner

from frame_slicer.cli import main


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


class TestSplit:
    def test_prints_one_line_per_slice(self) -> None:
        code, output = run("split", "1001-1240", "--slices", "8")
        assert code == 0
        lines = output.splitlines()
        assert len(lines) == 8
        assert lines[0] == "slice 1/8  1001-1030  (30 frames)"
        assert lines[1] == "slice 2/8  1031-1060  (30 frames)"
        assert lines[7] == "slice 8/8  1211-1240  (30 frames)"

    def test_numbers_the_slices_from_one_of_the_total(self) -> None:
        _, output = run("split", "1-10", "--slices", "3")
        assert output.splitlines() == [
            "slice 1/3  1-4  (4 frames)",
            "slice 2/3  5-7  (3 frames)",
            "slice 3/3  8-10  (3 frames)",
        ]

    def test_prints_a_single_slice_for_one_slice(self) -> None:
        code, output = run("split", "5-5", "--slices", "1")
        assert code == 0
        assert output.splitlines() == ["slice 1/1  5-5  (1 frame)"]

    def test_prints_nothing_but_the_slices_on_success(self) -> None:
        _, output = run("split", "1-4", "--slices", "2")
        assert output == "slice 1/2  1-2  (2 frames)\nslice 2/2  3-4  (2 frames)\n"

    def test_fails_with_usage_error_when_the_range_is_not_a_range(self) -> None:
        code, output = run("split", "abc", "--slices", "2")
        assert code == 2
        assert "START-END" in output
        assert "'abc'" in output

    def test_fails_when_the_range_ends_before_it_starts(self) -> None:
        code, output = run("split", "20-10", "--slices", "2")
        assert code == 2
        assert "before" in output

    def test_fails_when_there_are_more_slices_than_frames(self) -> None:
        code, output = run("split", "1-3", "--slices", "5")
        assert code == 2
        assert "more slices" in output

    def test_fails_when_the_slice_count_is_zero(self) -> None:
        code, output = run("split", "1-10", "--slices", "0")
        assert code == 2
        assert "at least 1" in output

    def test_fails_when_the_slice_count_is_missing(self) -> None:
        code, output = run("split", "1-10")
        assert code == 2
        assert "slice count or a chunk size" in output

    def test_fails_when_the_slice_count_is_not_a_number(self) -> None:
        code, _ = run("split", "1-10", "--slices", "many")
        assert code == 2

    def test_prints_no_slice_line_when_it_fails(self) -> None:
        _, output = run("split", "1-3", "--slices", "5")
        assert "slice 1/" not in output


class TestMain:
    def test_shows_the_version(self) -> None:
        code, output = run("--version")
        assert code == 0
        assert "frame-slicer" in output

    def test_lists_the_split_command_in_the_help(self) -> None:
        code, output = run("--help")
        assert code == 0
        assert "split" in output
