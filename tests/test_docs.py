"""The documents must not drift from the tool: links, ADR form, commands and the demo image."""

import re
import shlex
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from click.testing import CliRunner

from frame_slicer.cli import main

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
ADR_DIR = ROOT / "docs" / "adr"
ADR_FILES = sorted(ADR_DIR.glob("0*.md"))
ADR_SECTIONS = ("ステータス", "背景", "決定", "結果", "検討した代案")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def _relative_links(text: str) -> list[str]:
    return [link for link in LINK.findall(text) if not re.match(r"^[a-z]+:", link)]


@pytest.mark.parametrize(
    "document",
    [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "docs" / "spec.md", ADR_DIR / "README.md"]
    + ADR_FILES,
    ids=lambda path: path.name,
)
def test_every_relative_link_points_to_a_file_that_exists(document: Path) -> None:
    for link in _relative_links(document.read_text(encoding="utf-8")):
        assert (document.parent / link).exists(), f"{document.name} links to a missing {link}"


def test_there_are_at_least_three_adrs() -> None:
    assert len(ADR_FILES) >= 3


@pytest.mark.parametrize("adr", ADR_FILES, ids=lambda path: path.name)
def test_every_adr_has_the_agreed_sections(adr: Path) -> None:
    text = adr.read_text(encoding="utf-8")
    assert text.startswith("# ADR-")
    for section in ADR_SECTIONS:
        assert section in text, f"{adr.name} has no {section}"


def test_the_adr_index_lists_every_adr() -> None:
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    for adr in ADR_FILES:
        assert adr.name in index


def test_the_spec_describes_every_command() -> None:
    spec = (ROOT / "docs" / "spec.md").read_text(encoding="utf-8")
    for name in main.commands:  # type: ignore[attr-defined]
        assert f"`{name} " in spec, f"the spec does not describe {name}"


def test_the_readme_has_an_english_summary_and_an_image() -> None:
    assert "**English summary.**" in README
    assert "![" in README and "docs/images/" in README


def test_the_changelog_has_a_section_for_the_current_version() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    assert f"## [{version}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_the_demo_image_shows_what_the_tool_really_prints() -> None:
    namespace = "{http://www.w3.org/2000/svg}"
    root = ET.parse(ROOT / "docs" / "images" / "demo.svg").getroot()
    lines = [element.text or "" for element in root.iter(f"{namespace}text")]
    blocks: list[list[str]] = []
    for line in lines:
        if line.startswith("$ frame-slicer "):
            blocks.append([line])
        elif line:
            blocks[-1].append(line)
    assert blocks
    for block in blocks:
        arguments = shlex.split(block[0].removeprefix("$ frame-slicer "))
        result = CliRunner().invoke(main, arguments)
        assert result.exit_code == 0
        assert result.stdout.splitlines() == block[1:], block[0]
