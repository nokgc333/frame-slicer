"""Run a few real commands and draw their output as docs/images/demo.svg.

    python scripts/render_demo_svg.py

The picture is made from the tool's actual output, so it cannot drift from the tool.
"""

import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CLI = Path(sys.executable).parent / "frame-slicer"
OUTPUT = ROOT / "docs" / "images" / "demo.svg"
COMMANDS = [
    ["split", "1001-1240", "--slices", "4"],
    ["split", "1-10", "--chunk-size", "4"],
    ["submit", "1001-1240", "--slices", "4", "--name", "SHOT010_cache", "--dry-run"],
    ["commands", "1-12", "--slices", "2", "--tool", "blender", "--scene", "cube.blend"],
]
CHAR_WIDTH = 8.4
LINE_HEIGHT = 20
PADDING = 16


def run(args: list[str]) -> list[tuple[str, str]]:
    result = subprocess.run([str(CLI), *args], capture_output=True, text=True, check=True)
    lines = [("prompt", "$ frame-slicer " + " ".join(args))]
    lines += [("out", line) for line in result.stdout.splitlines()]
    return lines + [("out", "")]


def main() -> None:
    lines = [line for args in COMMANDS for line in run(args)][:-1]
    width = int(max(len(text) for _, text in lines) * CHAR_WIDTH + PADDING * 2 + CHAR_WIDTH)
    height = len(lines) * LINE_HEIGHT + PADDING * 2
    rows = []
    for index, (kind, text) in enumerate(lines):
        y = PADDING + (index + 1) * LINE_HEIGHT - 5
        color = "#7ee787" if kind == "prompt" else "#e6edf3"
        rows.append(f'<text x="{PADDING}" y="{y}" fill="{color}">{escape(text)}</text>')
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Output of the frame-slicer split, submit --dry-run and commands commands">\n'
        f'<rect width="{width}" height="{height}" rx="8" fill="#0d1117"/>\n'
        f'<g font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
        f'font-size="14" xml:space="preserve" style="white-space: pre">\n'
        + "\n".join(rows)
        + "\n</g>\n</svg>\n"
    )
    OUTPUT.write_text(svg, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
