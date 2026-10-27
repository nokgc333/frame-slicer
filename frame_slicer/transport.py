import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from frame_slicer.errors import QueueWatchError

TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 1_000_000


def normalize_base_url(url: str) -> str:
    """Accept only an http(s) address, and drop the trailing slash so paths can be appended."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise QueueWatchError(f"the QueueWatch address must be an http(s) URL, got {url!r}")
    return url.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, _req: Any, _fp: Any, _code: Any, _msg: Any, _headers: Any, _newurl: Any
    ) -> None:
        """Refuse every redirect: the queue's address is the only place a job is sent."""
        return None


def http_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST payload as JSON and return the JSON object the queue answers with.

    Every failure (HTTP error, unreachable host, slow answer, bad body) becomes a QueueWatchError.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as error:
        raise QueueWatchError(f"QueueWatch answered HTTP {error.code} for {url}") from error
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise QueueWatchError(f"could not reach {url}: {reason}") from error

    try:
        answer = json.loads(body)
    except ValueError as error:
        raise QueueWatchError(f"the answer from {url} was not JSON") from error
    if not isinstance(answer, dict):
        raise QueueWatchError(f"the answer from {url} was not a JSON object")
    return answer
