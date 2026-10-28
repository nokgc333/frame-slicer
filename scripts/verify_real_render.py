"""Slice -> submit -> a failed slice -> retry -> dependent stage, against a real QueueWatch worker.

Starts a private Redis, the QueueWatch API (token auth on) and a Celery worker that runs real
Blender, then drives frame-slicer's command line against them. Nothing outside a temporary
folder is touched. One job is made to fail on purpose (see FAULT_WRAPPER) to prove that retry
brings back only that slice.

    QUEUEWATCH_DIR=../queuewatch QW_PYTHON=<python with queuewatch's requirements> \\
    BLENDER_APP=/Applications/Blender.app python scripts/verify_real_render.py

Each result is one line: `CHECK <name> <PASS|FAIL> <detail>`. The exit code is 1 on any FAIL.
"""

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

QUEUEWATCH_DIR = Path(os.environ.get("QUEUEWATCH_DIR", "../queuewatch")).resolve()
QW_PYTHON = os.environ.get("QW_PYTHON", sys.executable)
BLENDER = (
    Path(os.environ.get("BLENDER_APP", "/Applications/Blender.app")) / "Contents/MacOS/Blender"
)
CLI = Path(sys.executable).parent / "frame-slicer"
SLICES = 4
FRAMES = "1-12"
FAILING_JOB_ID = 3
POLL_SECONDS = 1.0
TIMEOUT_SECONDS = int(os.environ.get("WAIT_SECONDS", "240"))
TERMINAL = ("success", "dead", "cancelled")

# A stand-in for a render node with a broken GPU driver: it crashes for one job's output folder
# while the flag file exists, and otherwise runs the real Blender unchanged.
FAULT_WRAPPER = """#!/bin/sh
if [ -e "{flag}" ]; then
  case "$*" in *job-{job}/*) echo "GPU driver crash (injected fault)" >&2; exit 139;; esac
fi
exec "{blender}" "$@"
"""

failures = 0
WORK = Path()
LOG_DIR = Path()


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += 0 if ok else 1
    print(f"CHECK {name} {'PASS' if ok else 'FAIL'} {detail}", flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until(condition: Callable[[], bool], what: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.3)
    raise SystemExit(f"timed out waiting for {what}")


def run_cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    shown = " ".join(["frame-slicer", *args]).replace(str(WORK), "<work>")
    print(f"\n$ {shown}", flush=True)
    result = subprocess.run([str(CLI), *args], env=env, capture_output=True, text=True, timeout=300)
    if result.stdout:
        print(result.stdout.rstrip().replace(str(WORK), "<work>"), flush=True)
    if result.stderr:
        print(f"[stderr] {result.stderr.rstrip()}".replace(str(WORK), "<work>"), flush=True)
    print(f"[exit code {result.returncode}]", flush=True)
    return result


def api_get(url: str, token: str) -> tuple[dict[str, str], object]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - local address
        return {k.lower(): v for k, v in response.headers.items()}, json.loads(response.read())


def statuses(manifest: Path, env: dict[str, str]) -> dict[str, str]:
    result = subprocess.run(
        [str(CLI), "status", str(manifest)], env=env, capture_output=True, text=True
    )
    rows = [line.split("  ") for line in result.stdout.splitlines()]
    return {row[0]: row[-1] for row in rows}


def wait_for_states(
    manifest: Path, env: dict[str, str], accept: Callable[[dict[str, str]], bool]
) -> dict[str, str]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        now = statuses(manifest, env)
        if now and accept(now):
            return now
        time.sleep(POLL_SECONDS)
    print(f"last states: {statuses(manifest, env)}", flush=True)
    for log in sorted(LOG_DIR.glob("*.log")):
        print(
            f"--- {log.name} (tail) ---\n"
            + "\n".join(log.read_text(errors="replace").splitlines()[-25:])
        )
    raise SystemExit(f"timed out waiting for the jobs of {manifest}")


def frames_in(renders: Path, job_id: int) -> int:
    return len(list((renders / f"job-{job_id}").glob("frame_*.png")))


def main() -> int:
    version = subprocess.run([str(BLENDER), "-b", "--version"], capture_output=True, text=True)
    print(f"BLENDER_VERSION {version.stdout.splitlines()[0]}")
    commit = subprocess.run(
        ["git", "-C", str(QUEUEWATCH_DIR), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    print(f"QUEUEWATCH_COMMIT {commit}")

    token = secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        global WORK
        WORK = work
        scenes, renders = work / "scenes", work / "renders"
        scenes.mkdir()
        subprocess.run(
            [str(BLENDER), "-b", "--factory-startup", "--python"]
            + [
                str(QUEUEWATCH_DIR / "scripts/make_sample_scene.py"),
                "--",
                str(scenes / "cube.blend"),
            ],
            check=True,
            capture_output=True,
        )
        flag = work / "fail-flag"
        flag.write_text("on")
        wrapper = work / "blender-wrapper.sh"
        wrapper.write_text(FAULT_WRAPPER.format(flag=flag, job=FAILING_JOB_ID, blender=BLENDER))
        wrapper.chmod(0o755)

        redis_port, api_port = free_port(), free_port()
        redis_url = f"redis://127.0.0.1:{redis_port}/0"
        server_env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{work / 'queue.db'}",
            "CELERY_BROKER_URL": redis_url,
            "CELERY_RESULT_BACKEND": redis_url,
            "RENDERER": "blender",
            "BLENDER_PATH": str(wrapper),
            "RENDER_SCENE_ROOT": str(scenes),
            "RENDER_OUTPUT_ROOT": str(renders),
            "RENDER_TIMEOUT_SECONDS": "120",
            "QUEUEWATCH_AUTH_MODE": "token",
            "QUEUEWATCH_API_TOKENS": f"editor={token}",
        }
        client_env = {
            **os.environ,
            "QUEUEWATCH_API_URL": f"http://127.0.0.1:{api_port}",
            "QUEUEWATCH_API_TOKEN": token,
        }
        backend = QUEUEWATCH_DIR / "backend"
        logs = work / "logs"
        logs.mkdir()
        global LOG_DIR
        LOG_DIR = logs
        processes: list[subprocess.Popen[bytes]] = []

        def start(name: str, *command: str) -> None:
            log = open(logs / f"{name}.log", "wb")
            processes.append(
                subprocess.Popen(command, cwd=backend, env=server_env, stdout=log, stderr=log)
            )

        try:
            start(
                "redis",
                "redis-server",
                "--port",
                str(redis_port),
                "--save",
                "",
                "--appendonly",
                "no",
            )
            wait_until(lambda: socket.socket().connect_ex(("127.0.0.1", redis_port)) == 0, "redis")
            subprocess.run(
                [QW_PYTHON, "-m", "app.migrate"], cwd=backend, env=server_env, check=True
            )
            start("api", QW_PYTHON, "-m", "uvicorn", "app.main:app", "--port", str(api_port))
            # macOS cannot start Celery's default prefork pool (spawn), so run one process here.
            # The Docker image uses prefork on Linux.
            start(
                "worker", QW_PYTHON, "-m", "celery", "-A", "app.worker", "worker",
                "-Q", "queue_high,queue_normal,queue_low", "--pool=solo", "--loglevel=info",
            )  # fmt: skip
            base = f"http://127.0.0.1:{api_port}"
            wait_until(lambda: _healthy(base), "the API")
            time.sleep(4)  # let the worker connect to the broker

            cache, render = work / "cache.json", work / "render.json"
            args = ("--slices", str(SLICES), "--scene", "cube.blend")

            print("\n== 1. slice and submit; one slice will fail on purpose ==")
            first = run_cli(
                client_env, "submit", FRAMES, *args, "--name", "cube", "--manifest", str(cache)
            )
            check(
                "submit-all-slices",
                first.returncode == 0 and "4 of 4 jobs submitted" in first.stdout,
            )
            states = wait_for_states(
                cache, client_env, lambda s: all(v in TERMINAL for v in s.values())
            )
            counts = sorted(states.values())
            check(
                "one-slice-died", counts == ["dead", "success", "success", "success"], str(counts)
            )
            check("good-slices-have-frames", all(frames_in(renders, j) == 3 for j in (1, 2, 4)))

            print("\n== 2. a dependent stage waits for the cache and stops at the dead slice ==")
            blocked = run_cli(
                client_env, "submit", FRAMES, *args, "--name", "lit",
                "--depends-on", str(cache), "--wait-timeout", "30", "--manifest", str(render),
            )  # fmt: skip
            check("dependent-stage-blocked", blocked.returncode == 1 and "dead" in blocked.stderr)
            sent = json.loads(render.read_text())["jobs"]
            check(
                "only-ready-slices-were-sent",
                [j["id"] is not None for j in sent] == [True, True, False, False],
            )

            print("\n== 3. the fault is fixed; retry brings back only the dead slice ==")
            flag.unlink()
            retried = run_cli(client_env, "retry", str(cache))
            check("retry-requeued-the-dead-slice", "requeued cube_7-9 (id=3)" in retried.stdout)
            wait_for_states(cache, client_env, lambda s: all(v == "success" for v in s.values()))
            check("retried-slice-has-frames", frames_in(renders, FAILING_JOB_ID) == 3)
            headers, _ = api_get(f"{base}/jobs", token)
            check(
                "no-duplicate-jobs",
                headers.get("x-total-count") == "6",
                f"total={headers.get('x-total-count')}",
            )

            print("\n== 4. the dependent stage continues now that the cache is whole ==")
            done = run_cli(client_env, "retry", str(render))
            check("dependent-stage-completed", done.returncode == 0)
            wait_for_states(render, client_env, lambda s: all(v == "success" for v in s.values()))
            rendered = [frames_in(renders, j) for j in range(1, 9)]
            check("every-job-wrote-its-frames", rendered == [3] * 8, str(rendered))

            print("\n== 5. the same range as render commands (printed, not run) ==")
            run_cli(
                client_env,
                "commands",
                FRAMES,
                "--slices",
                "2",
                "--tool",
                "blender",
                "--scene",
                "cube.blend",
            )
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            shutil.rmtree(logs, ignore_errors=True)
    return 1 if failures else 0


def _healthy(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/healthz", timeout=2) as response:  # noqa: S310
            return bool(response.status == 200)
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
