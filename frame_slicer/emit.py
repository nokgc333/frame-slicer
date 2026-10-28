import re

from frame_slicer.errors import QueueWatchError
from frame_slicer.ranges import FrameRange

TOOLS = ("blender", "husk", "hbatch")

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_ROP_PATH = re.compile(r"/[A-Za-z0-9_./-]+")


def _check_scene(scene: str) -> None:
    if not scene.strip() or scene.startswith("-") or _CONTROL_CHARACTERS.search(scene):
        raise QueueWatchError(
            "the scene must be a path that does not start with '-' or hold control characters"
        )


def _blender(
    executable: str, scene: str, frames: FrameRange, output: str | None, rop: str | None
) -> list[str]:
    command = [executable, "-b", scene, "-noaudio"]
    if output is not None:
        command += ["-o", output]
    command += ["-s", str(frames.start), "-e", str(frames.end)]
    if frames.step > 1:
        command += ["-j", str(frames.step)]
    return command + ["-a"]


def _husk(
    executable: str, scene: str, frames: FrameRange, output: str | None, rop: str | None
) -> list[str]:
    command = [executable, "-f", str(frames.start), "-n", str(frames.count)]
    command += ["-i", str(frames.step)]
    if output is not None:
        command += ["-o", output]
    return command + [scene]


def _hbatch(
    executable: str, scene: str, frames: FrameRange, output: str | None, rop: str | None
) -> list[str]:
    if rop is None or not _ROP_PATH.fullmatch(rop):
        raise QueueWatchError(
            "hbatch needs --rop, the path of one output driver such as /out/mantra1"
            " (letters, digits, _ . - and / only)"
        )
    render = f"render -V -f {frames.start} {frames.end} -i {frames.step} {rop}; quit"
    return [executable, "-c", render, scene]


_BUILDERS = {"blender": _blender, "husk": _husk, "hbatch": _hbatch}


def build_command(
    tool: str,
    scene: str,
    frames: FrameRange,
    output: str | None = None,
    rop: str | None = None,
    executable: str | None = None,
) -> list[str]:
    """The command line that renders `frames` of `scene` with `tool`, as a list (no shell).

    The command is only built, never run: the person decides where and how to run it.
    """
    if tool not in _BUILDERS:
        raise QueueWatchError(f"unknown tool {tool!r}; choose one of {', '.join(TOOLS)}")
    _check_scene(scene)
    return _BUILDERS[tool](executable or tool, scene, frames, output, rop)
