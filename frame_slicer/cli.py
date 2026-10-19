import click

from frame_slicer.queuewatch import (
    DEFAULT_API_BASE_URL,
    VALID_PRIORITIES,
    Post,
    QueueWatchError,
    build_jobs,
    http_post,
    normalize_base_url,
    submit_jobs,
)
from frame_slicer.ranges import FrameRangeError, format_slice, parse_range, split_range

SLICES_OPTION = click.option(
    "--slices", required=True, type=int, help="How many slices to cut the range into."
)


@click.group()
@click.version_option(package_name="frame-slicer", prog_name="frame-slicer")
def main() -> None:
    """Split a frame range into equal slices, and submit them to QueueWatch."""


@main.command()
@click.argument("frames")
@SLICES_OPTION
def split(frames: str, slices: int) -> None:
    """Print the slices of FRAMES (START-END, both ends included).

    Use it for work where every frame is independent, such as writing caches.
    A simulation depends on the previous frame, so it must not be split by frames.
    """
    try:
        parts = split_range(parse_range(frames), slices)
    except FrameRangeError as error:
        raise click.UsageError(str(error)) from error

    for index, part in enumerate(parts, start=1):
        click.echo(format_slice(index, len(parts), part))


@main.command()
@click.argument("frames")
@SLICES_OPTION
@click.option("--name", required=True, help="Base name of the jobs; the slice range is appended.")
@click.option(
    "--priority",
    type=click.Choice(VALID_PRIORITIES),
    default="normal",
    show_default=True,
    help="Priority of every job.",
)
@click.option(
    "--api-base-url",
    default=DEFAULT_API_BASE_URL,
    show_default=True,
    help="Address of the QueueWatch API.",
)
@click.option("--dry-run", is_flag=True, help="Show the jobs without sending them.")
@click.pass_obj
def submit(
    post: Post | None,
    frames: str,
    slices: int,
    name: str,
    priority: str,
    api_base_url: str,
    dry_run: bool,
) -> None:
    """Split FRAMES and submit one QueueWatch job per slice.

    Each job is named NAME_START-END. QueueWatch only stores the name and the priority;
    it does not read the frame range itself.
    """
    try:
        jobs = build_jobs(name, split_range(parse_range(frames), slices), priority)
        base_url = normalize_base_url(api_base_url)
    except (FrameRangeError, QueueWatchError) as error:
        raise click.UsageError(str(error)) from error

    if dry_run:
        for job in jobs:
            click.echo(f"[dry-run] {job.name} (priority={job.priority})")
        click.echo(f"[dry-run] {len(jobs)} jobs would be submitted to {base_url}")
        return

    submitted = 0
    try:
        for result in submit_jobs(base_url, jobs, post or http_post):
            click.echo(f"submitted {result.name} (id={result.id}, status={result.status})")
            submitted += 1
    except QueueWatchError as error:
        click.echo(f"Error: {error}", err=True)
        click.echo(f"{submitted} of {len(jobs)} jobs submitted", err=True)
        raise click.exceptions.Exit(1) from error

    click.echo(f"{submitted} of {len(jobs)} jobs submitted")
