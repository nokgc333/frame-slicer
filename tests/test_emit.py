import shlex

import pytest

from frame_slicer.emit import TOOLS, build_command
from frame_slicer.errors import QueueWatchError
from frame_slicer.ranges import FrameRange


class TestBlender:
    def test_renders_the_range_in_the_background(self) -> None:
        assert build_command("blender", "s.blend", FrameRange(1, 10)) == [
            "blender", "-b", "s.blend", "-noaudio", "-s", "1", "-e", "10", "-a",
        ]  # fmt: skip

    def test_adds_the_step_with_frame_jump(self) -> None:
        command = build_command("blender", "s.blend", FrameRange(1, 9, 2))
        assert command[-3:] == ["-j", "2", "-a"]
        assert command[command.index("-s") + 1] == "1"

    def test_puts_the_output_before_the_render_flag(self) -> None:
        command = build_command("blender", "s.blend", FrameRange(1, 2), output="out/f_####")
        assert command.index("-o") < command.index("-a")
        assert command[command.index("-o") + 1] == "out/f_####"

    def test_uses_the_executable_it_is_given(self) -> None:
        command = build_command(
            "blender", "s.blend", FrameRange(1, 2), executable="/opt/bl/blender"
        )
        assert command[0] == "/opt/bl/blender"


class TestHusk:
    def test_renders_a_count_of_frames_from_the_start(self) -> None:
        assert build_command("husk", "s.usd", FrameRange(1001, 1030)) == [
            "husk", "-f", "1001", "-n", "30", "-i", "1", "s.usd",
        ]  # fmt: skip

    def test_counts_only_the_rendered_frames_of_a_stepped_range(self) -> None:
        command = build_command("husk", "s.usd", FrameRange(1, 99, 2), output="o.$F4.exr")
        assert command[command.index("-n") + 1] == "50"
        assert command[command.index("-i") + 1] == "2"
        assert command[command.index("-o") + 1] == "o.$F4.exr"
        assert command[-1] == "s.usd"


class TestHbatch:
    def test_renders_one_output_driver_over_the_range(self) -> None:
        command = build_command("hbatch", "s.hip", FrameRange(1, 9, 2), rop="/out/mantra1")
        assert command == [
            "hbatch", "-c", "render -V -f 1 9 -i 2 /out/mantra1; quit", "s.hip",
        ]  # fmt: skip

    def test_needs_the_name_of_the_output_driver(self) -> None:
        with pytest.raises(QueueWatchError, match="rop"):
            build_command("hbatch", "s.hip", FrameRange(1, 2))

    @pytest.mark.parametrize("rop", ["/out/a; quit", "out/a", "/out/a b", "/out/$x", "/out/a\n"])
    def test_refuses_a_driver_name_that_could_add_commands(self, rop: str) -> None:
        with pytest.raises(QueueWatchError, match="rop"):
            build_command("hbatch", "s.hip", FrameRange(1, 2), rop=rop)


class TestInput:
    def test_knows_three_tools(self) -> None:
        assert set(TOOLS) == {"blender", "husk", "hbatch"}

    def test_refuses_an_unknown_tool(self) -> None:
        with pytest.raises(QueueWatchError, match="tool"):
            build_command("maya", "s.ma", FrameRange(1, 2))

    @pytest.mark.parametrize("scene", ["", "-evil.blend", "a\nb.blend", " "])
    def test_refuses_a_scene_that_could_be_read_as_an_option(self, scene: str) -> None:
        with pytest.raises(QueueWatchError, match="scene"):
            build_command("blender", scene, FrameRange(1, 2))

    def test_a_command_with_spaces_survives_shell_quoting(self) -> None:
        command = build_command("blender", "my shots/s 1.blend", FrameRange(1, 2))
        assert shlex.split(shlex.join(command)) == command
