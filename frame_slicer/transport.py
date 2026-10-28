import hashlib
import json
import random
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from frame_slicer.errors import QueueWatchError

DEFAULT_API_BASE_URL = "http://localhost:8000"
TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 1_000_000
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 8.0
MAX_RETRY_AFTER_SECONDS = 30.0
RETRY_STATUSES = (429, 503)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

Post = Callable[[str, dict[str, Any]], dict[str, Any]]
Get = Callable[[str], dict[str, Any]]


class _Transient(Exception):
    """A failure after which the queue certainly did not take the job, so a retry is safe."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def normalize_base_url(url: str) -> str:
    """Accept only an http(s) address, and drop the trailing slash so paths can be appended."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise QueueWatchError(f"the QueueWatch address must be an http(s) URL, got {url!r}")
    return url.rstrip("/")


def backoff_delay(attempt: int, jitter: Callable[[], float] = random.random) -> float:
    """Wait for retry number `attempt` (0 for the first): full jitter below a capped exponential."""
    ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * float(2**attempt))
    return jitter() * ceiling


def idempotency_key(url: str, payload: dict[str, Any]) -> str:
    """A key that is the same for the same job on the same queue, however often it is sent."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{url}\n{canonical}".encode()).hexdigest()[:32]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # noqa: D102
        self, _req: Any, _fp: Any, _code: Any, _msg: Any, _headers: Any, _newurl: Any
    ) -> None:
        """Refuse every redirect: the queue's address is the only place a job is sent."""
        return None


def _check_token(url: str, token: str | None) -> None:
    if token is None:
        return
    if _CONTROL_CHARACTERS.search(token):
        raise QueueWatchError("the API token must not contain control characters")
    parsed = urlparse(url)
    if parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS:
        raise QueueWatchError(
            "refusing to send the API token over plain http to a host other than this machine; "
            "use an https address"
        )


def _retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers is not None else None
    if value is None or not value.strip().isdigit():
        return None
    return min(float(value), MAX_RETRY_AFTER_SECONDS)


def _send_once(request: urllib.request.Request, url: str, read_limit: int) -> bytes:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            body: bytes = response.read(read_limit + 1)
    except urllib.error.HTTPError as error:
        message = f"QueueWatch answered HTTP {error.code} for {url}"
        if error.code in RETRY_STATUSES:
            raise _Transient(message, _retry_after(error.headers)) from error
        raise QueueWatchError(message) from error
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        message = f"could not reach {url}: {reason}"
        if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
            raise _Transient(message) from error
        raise QueueWatchError(message) from error
    if len(body) > read_limit:
        raise QueueWatchError(f"the answer from {url} is larger than {read_limit} bytes")
    return body


def _parse_answer(body: bytes, url: str) -> dict[str, Any]:
    try:
        answer = json.loads(body)
    except ValueError as error:
        raise QueueWatchError(f"the answer from {url} was not JSON") from error
    if not isinstance(answer, dict):
        raise QueueWatchError(f"the answer from {url} was not a JSON object")
    return answer


def _request(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    token: str | None,
    read_limit: int,
    sleep: Callable[[float], None],
    jitter: Callable[[], float],
) -> dict[str, Any]:
    _check_token(url, token)
    headers: dict[str, str] = {}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = idempotency_key(url, payload)
        data = json.dumps(payload).encode("utf-8")
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return _parse_answer(_send_once(request, url, read_limit), url)
        except _Transient as error:
            if attempt == MAX_ATTEMPTS - 1:
                raise QueueWatchError(f"{error} (gave up after {MAX_ATTEMPTS} attempts)") from error
            wait = error.retry_after
            sleep(backoff_delay(attempt, jitter) if wait is None else wait)
    raise AssertionError("unreachable")  # pragma: no cover - the loop always returns or raises


def http_post(
    url: str,
    payload: dict[str, Any],
    *,
    token: str | None = None,
    read_limit: int = MAX_RESPONSE_BYTES,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> dict[str, Any]:
    """POST payload as JSON and return the JSON object the queue answers with.

    Only a failure that leaves no job behind (connection refused, HTTP 429 or 503) is retried,
    with a capped exponential backoff. A slow answer is not: the queue may already hold the job.
    Every attempt carries the same Idempotency-Key. Every failure becomes a QueueWatchError.
    """
    return _request("POST", url, payload, token, read_limit, sleep, jitter)


def http_get(
    url: str,
    *,
    token: str | None = None,
    read_limit: int = MAX_RESPONSE_BYTES,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> dict[str, Any]:
    """GET a JSON object, with the same limits, retries and token rules as http_post."""
    return _request("GET", url, None, token, read_limit, sleep, jitter)
