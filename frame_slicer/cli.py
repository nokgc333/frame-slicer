import os
import shlex
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import click

from frame_slicer.emit import TOOLS, build_command
from frame_slicer.errors import QueueWatchError
from frame_slicer.jobs import VALID_PRIORITIES, build_jobs
from frame_slicer.manifest import Manifest, load_manifest, new_manifest, save_manifest
from frame_slicer.ranges import (
    FrameRange,
    FrameRangeError,
    format_slice,
    heavy_first,
    parse_ranges,
    plan_slices,
)
from frame_slicer.transport import (
    DEFAULT_API_BASE_URL,
    http_get,
    http_post,
    normalize_base_url,
)
from frame_slicer.workflow import (
    Api,
    check_dependencies,
    retry_all,
    submit_all,
    wait_for_dependency,
)

DEFAULT_WAIT_TIMEOUT_SECONDS = 3600
DEFAULT_POLL_INTERVAL_SECONDS = 2.0

CUT_OPTIONS = [
    click.option("--slices", type=int, default=None, help="Cut one range into this many slices."),
    click.option(
        "--chunk-size", type=int, default=None, help="Cut every range into chunks of this size."
    ),
    click.option(
        "--heavy",
        default=None,
        help="Frames that take long (e.g. 1050-1060); the slices holding them go first.",
    ),
]
MANIFEST_ARGUMENT = click.argument("manifest", type=click.Path(path_type=Path))


def _with_cut_options(function: Callable[..., Any]) -> Callable[..., Any]:
    for option in reversed(CUT_OPTIONS):
        function = option(function)
    return function


def _token() -> str | None:
    """The API token from the environment; an empty value means no token."""
    return os.environ.get("QUEUEWATCH_API_TOKEN", "").strip() or None


def _api(api: Api | None) -> Api:
    token = _token()
    return api or Api(partial(http_post, token=token), partial(http_get, token=token))


def _plan(
    frames: str, slices: int | None, chunk_size: int | None, heavy: str | None
) -> list[FrameRange]:
    try:
        parts = plan_slices(parse_ranges(frames), slices, chunk_size)
        return heavy_first(parts, parse_ranges(heavy)) if heavy else parts
    except FrameRangeError as error:
        raise click.UsageError(str(error)) from error


def _count_submitted(manifest: Manifest) -> int:
    return sum(record.id is not None for record in manifest.jobs)


def _finish(manifest: Manifest, error: QueueWatchError | None, path: Path | None) -> None:
    total = len(manifest.jobs)
    if error is None:
        click.echo(f"{_count_submitted(manifest)} of {total} jobs submitted")
        return
    click.echo(f"Error: {error}", err=True)
    click.echo(f"{_count_submitted(manifest)} of {total} jobs submitted", err=True)
    if path is not None:
        click.echo(f"The manifest {path} keeps the state; run: frame-slicer retry {path}", err=True)
    raise click.exceptions.Exit(1) from error


def _saver(path: Path | None) -> Callable[[Manifest], None]:
    if path is None:
        return lambda manifest: None
    return lambda manifest: save_manifest(path, manifest)


@click.group()
@click.version_option(package_name="frame-slicer", prog_name="frame-slicer")
def main() -> None:
    """Split frame ranges into slices, and submit them to QueueWatch."""


@main.command()
@click.argument("frames")
@_with_cut_options
def split(frames: str, slices: int | None, chunk_size: int | None, heavy: str | None) -> None:
    """Print the slices of FRAMES.

    FRAMES is START-END (both ends included), START-ENDxSTEP for every STEP-th frame,
    or several of these separated by commas.

    Use it for work where every frame is independent, such as writing caches.
    A simulation depends on the previous frame, so it must not be split by frames.
    """
    parts = _plan(frames, slices, chunk_size, heavy)
    for index, part in enumerate(parts, start=1):
        click.echo(format_slice(index, len(parts), part))


@main.command()
@click.argument("frames")
@_with_cut_options
@click.option("--name", required=True, help="Base name of the jobs; the slice range is appended.")
@click.option(
    "--priority",
    type=click.Choice(VALID_PRIORITIES),
    default="normal",
    show_default=True,
    help="Priority of every job.",
)
@click.option("--scene", default=None, help="Scene path the queue renders (a .blend file).")
@click.option(
    "--api-base-url",
    envvar="QUEUEWATCH_API_URL",
    default=DEFAULT_API_BASE_URL,
    show_default=True,
    help="Address of the QueueWatch API (or set QUEUEWATCH_API_URL).",
)
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the result here (JSON); it must not exist yet. Use retry to continue it.",
)
@click.option(
    "--depends-on",
    "depends_on",
    type=click.Path(path_type=Path),
    default=None,
    help="Manifest of earlier jobs; each job waits for the one with the same frames to succeed.",
)
@click.option("--wait-timeout", type=float, default=DEFAULT_WAIT_TIMEOUT_SECONDS, show_default=True)
@click.option("--dry-run", is_flag=True, help="Show the jobs without sending them.")
@click.pass_obj
def submit(
    api: Api | None,
    frames: str,
    slices: int | None,
    chunk_size: int | None,
    heavy: str | None,
    name: str,
    priority: str,
    scene: str | None,
    api_base_url: str,
    manifest_path: Path | None,
    depends_on: Path | None,
    wait_timeout: float,
    dry_run: bool,
) -> None:
    """Split FRAMES and submit one QueueWatch job per slice.

    Each job is named NAME_START-END and carries its frame range (and the scene, if given).
    A step cannot be submitted: the queue renders whole ranges. Use the commands command.

    If the QUEUEWATCH_API_TOKEN environment variable is set, it is sent as a Bearer token
    (never over plain http to another machine, and never printed).
    """
    parts = _plan(frames, slices, chunk_size, heavy)
    try:
        jobs = build_jobs(name, parts, priority, scene)
        base_url = normalize_base_url(api_base_url)
        manifest = new_manifest(base_url, jobs)
        dependency = load_manifest(depends_on) if depends_on else None
        if dependency is not None:
            check_dependencies(dependency, manifest)
    except QueueWatchError as error:
        raise click.UsageError(str(error)) from error

    if dry_run:
        for job in jobs:
            click.echo(f"[dry-run] {job.name} (priority={job.priority})")
        click.echo(f"[dry-run] {len(jobs)} jobs would be submitted to {base_url}")
        return

    if manifest_path is not None and manifest_path.exists():
        raise click.UsageError(
            f"{manifest_path} already exists; to continue that submission run:"
            f" frame-slicer retry {manifest_path}"
        )
    queue = _api(api)
    wait_for = (
        wait_for_dependency(queue, dependency, wait_timeout, DEFAULT_POLL_INTERVAL_SECONDS)
        if dependency
        else None
    )
    save = _saver(manifest_path)
    try:
        save(manifest)
    except QueueWatchError as error:
        raise click.UsageError(str(error)) from error
    done, failure = submit_all(queue, manifest, save, click.echo, wait_for)
    _finish(done, failure, manifest_path)


@main.command()
@MANIFEST_ARGUMENT
@click.pass_obj
def retry(api: Api | None, manifest: Path) -> None:
    """Send the jobs of MANIFEST that were never accepted, and requeue the dead or cancelled ones.

    A requeued job keeps its id, so no slice is ever submitted twice.
    """
    try:
        loaded = load_manifest(manifest)
    except QueueWatchError as error:
        raise click.UsageError(str(error)) from error
    done, failure = retry_all(_api(api), loaded, _saver(manifest), click.echo)
    _finish(done, failure, manifest)


@main.command()
@MANIFEST_ARGUMENT
@click.pass_obj
def status(api: Api | None, manifest: Path) -> None:
    """Show the state of every job of MANIFEST, as the queue reports it now."""
    try:
        loaded = load_manifest(manifest)
        queue = _api(api)
        base = f"{normalize_base_url(loaded.api_base_url)}/jobs"
        for record in loaded.jobs:
            if record.id is None:
                click.echo(f"{record.name}  not submitted")
                continue
            now = queue.get(f"{base}/{record.id}").get("status")
            click.echo(f"{record.name}  id={record.id}  {now}")
    except QueueWatchError as error:
        click.echo(f"Error: {error}", err=True)
        raise click.exceptions.Exit(1) from error


@main.command()
@click.argument("frames")
@_with_cut_options
@click.option("--tool", type=click.Choice(TOOLS), required=True, help="Renderer to run.")
@click.option("--scene", required=True, help="Scene file the command opens.")
@click.option("--output", default=None, help="Output image path or pattern.")
@click.option("--rop", default=None, help="Output driver for hbatch, e.g. /out/mantra1.")
@click.option("--executable", default=None, help="Program to run instead of the tool's name.")
def commands(
    frames: str,
    slices: int | None,
    chunk_size: int | None,
    heavy: str | None,
    tool: str,
    scene: str,
    output: str | None,
    rop: str | None,
    executable: str | None,
) -> None:
    """Print one render command per slice of FRAMES (they are printed, never run)."""
    parts = _plan(frames, slices, chunk_size, heavy)
    try:
        for part in parts:
            click.echo(shlex.join(build_command(tool, scene, part, output, rop, executable)))
    except QueueWatchError as error:
        raise click.UsageError(str(error)) from error
