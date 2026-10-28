import time
from collections.abc import Callable
from dataclasses import dataclass

from frame_slicer.errors import QueueWatchError
from frame_slicer.manifest import JobRecord, Manifest
from frame_slicer.ranges import format_range
from frame_slicer.transport import Get, Post, normalize_base_url

Save = Callable[[Manifest], None]
Report = Callable[[str], None]
WaitFor = Callable[[JobRecord], None]

SUCCESS = "success"
CANNOT_SUCCEED = ("dead", "cancelled")


@dataclass(frozen=True)
class Api:
    """The two things the tool does to the queue; tests replace them with fakes."""

    post: Post
    get: Get


def _jobs_url(manifest: Manifest) -> str:
    return f"{normalize_base_url(manifest.api_base_url)}/jobs"


def _submit_one(api: Api, manifest: Manifest, index: int) -> JobRecord:
    record = manifest.jobs[index]
    answer = api.post(_jobs_url(manifest), manifest.job_request(record).payload())
    job_id, status = answer.get("id"), answer.get("status")
    if not isinstance(job_id, int) or isinstance(job_id, bool) or not isinstance(status, str):
        raise QueueWatchError(f"unexpected answer for {record.name!r}: {answer!r}")
    return record.submitted(job_id, status)


def submit_all(
    api: Api,
    manifest: Manifest,
    save: Save,
    report: Report,
    wait_for: WaitFor | None = None,
) -> tuple[Manifest, QueueWatchError | None]:
    """Send every job that has no id yet, saving the manifest after each one.

    Stops at the first failure: the jobs already accepted stay, and the reason is recorded.
    """
    for index, record in enumerate(manifest.jobs):
        if record.id is not None:
            continue
        try:
            if wait_for is not None:
                wait_for(record)
            record = _submit_one(api, manifest, index)
        except QueueWatchError as error:
            manifest = manifest.with_record(index, manifest.jobs[index].failed(str(error)))
            save(manifest)
            return manifest, error
        manifest = manifest.with_record(index, record)
        save(manifest)
        report(f"submitted {record.name} (id={record.id}, status={record.status})")
    return manifest, None


def _requeue(api: Api, manifest: Manifest, record: JobRecord) -> JobRecord:
    answer = api.post(f"{_jobs_url(manifest)}/{record.id}/requeue", {})
    status = answer.get("status")
    if not isinstance(status, str):
        raise QueueWatchError(f"unexpected answer for {record.name!r}: {answer!r}")
    return record.with_status(status)


def retry_all(
    api: Api, manifest: Manifest, save: Save, report: Report
) -> tuple[Manifest, QueueWatchError | None]:
    """Send what was never accepted, and requeue what died or was cancelled; leave the rest.

    A requeued job keeps its id, so retrying never creates a second copy of a slice.
    """
    for index, record in enumerate(manifest.jobs):
        try:
            if record.id is None:
                updated = _submit_one(api, manifest, index)
                message = f"submitted {updated.name} (id={updated.id}, status={updated.status})"
            else:
                status = api.get(f"{_jobs_url(manifest)}/{record.id}").get("status")
                if not isinstance(status, str):
                    raise QueueWatchError(f"unexpected answer for {record.name!r}")
                updated = record.with_status(status)
                manifest = manifest.with_record(index, updated)
                if status in CANNOT_SUCCEED:
                    updated = _requeue(api, manifest, updated)
                    message = f"requeued {updated.name} (id={updated.id})"
                else:
                    message = f"kept {updated.name} (id={updated.id}, status={updated.status})"
        except QueueWatchError as error:
            manifest = manifest.with_record(index, manifest.jobs[index].failed(str(error)))
            save(manifest)
            return manifest, error
        manifest = manifest.with_record(index, updated)
        save(manifest)
        report(message)
    return manifest, None


def check_dependencies(dependency: Manifest, manifest: Manifest) -> None:
    """Every job needs a partner with exactly the same frames in the dependency manifest."""
    missing = [r for r in manifest.jobs if dependency.find(r.frames) is None]
    if missing:
        ranges = ", ".join(format_range(r.frames) for r in missing)
        raise QueueWatchError(f"the dependency manifest has no job for the frames {ranges}")


def wait_for_dependency(
    api: Api,
    dependency: Manifest,
    timeout_seconds: float,
    interval_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> WaitFor:
    """A wait that holds a job back until the dependency's job for the same frames succeeded.

    The time limit applies to each wait, not to the whole run.
    """

    def wait(record: JobRecord) -> None:
        started = clock()
        index = dependency.find(record.frames)
        if index is None:
            raise QueueWatchError(f"no dependency job for {format_range(record.frames)}")
        partner = dependency.jobs[index]
        if partner.id is None:
            raise QueueWatchError(f"the dependency job {partner.name} was never submitted")
        url = f"{_jobs_url(dependency)}/{partner.id}"
        while True:
            status = api.get(url).get("status")
            if status == SUCCESS:
                return
            if status in CANNOT_SUCCEED:
                raise QueueWatchError(f"the dependency job {partner.name} is {status}")
            if clock() - started > timeout_seconds:
                raise QueueWatchError(
                    f"the dependency job {partner.name} did not succeed "
                    f"within {timeout_seconds:g} seconds"
                )
            sleep(interval_seconds)

    return wait
