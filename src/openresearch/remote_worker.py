from __future__ import annotations

import json
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .contracts import JobRequest, canonical_json, sha256_json, utc_now


def _run_checked(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed with exit {result.returncode}: {argv!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def execute(payload: dict[str, Any], jobs_root: Path) -> dict[str, Any]:
    request = JobRequest.from_payload(payload)
    job_dir = jobs_root.resolve() / request.job_id
    checkout = job_dir / "checkout"
    result_dir = job_dir / "result"
    if job_dir.exists():
        raise FileExistsError(f"refusing to reuse existing job directory: {job_dir}")
    result_dir.mkdir(parents=True)
    (result_dir / "request.json").write_text(canonical_json(payload) + "\n", encoding="utf-8")

    started_at = utc_now()
    started_monotonic = time.monotonic()
    completion: dict[str, Any]
    try:
        checkout_source = request.checkout_source or request.repository
        _run_checked(["git", "clone", "--no-checkout", checkout_source, str(checkout)])
        _run_checked(["git", "fetch", "--force", "origin", request.commit_sha], cwd=checkout)
        _run_checked(["git", "checkout", "--detach", request.commit_sha], cwd=checkout)
        checked_out = _run_checked(["git", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
        if checked_out != request.commit_sha:
            raise RuntimeError(f"checkout mismatch: requested {request.commit_sha}, got {checked_out}")

        process = subprocess.run(
            list(request.command),
            cwd=checkout,
            text=True,
            capture_output=True,
            timeout=request.timeout_seconds,
            check=False,
        )
        (result_dir / "stdout.log").write_text(process.stdout, encoding="utf-8")
        (result_dir / "stderr.log").write_text(process.stderr, encoding="utf-8")
        status = "succeeded" if process.returncode == 0 else "failed"
        completion = {
            "schema": "openresearch.remote-completion.v1",
            "status": status,
            "job_id": request.job_id,
            "task_id": request.task_id,
            "request_sha256": payload["request_sha256"],
            "requested_commit": request.commit_sha,
            "checked_out_commit": checked_out,
            "checkout_source": checkout_source,
            "command": list(request.command),
            "exit_code": process.returncode,
            "started_at": started_at,
            "finished_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started_monotonic, 6),
            "runtime": {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "python": platform.python_version(),
            },
        }
    except subprocess.TimeoutExpired as error:
        (result_dir / "stdout.log").write_text(error.stdout or "", encoding="utf-8")
        (result_dir / "stderr.log").write_text(error.stderr or "", encoding="utf-8")
        completion = {
            "schema": "openresearch.remote-completion.v1",
            "status": "timed_out",
            "job_id": request.job_id,
            "task_id": request.task_id,
            "request_sha256": payload["request_sha256"],
            "requested_commit": request.commit_sha,
            "checked_out_commit": request.commit_sha,
            "checkout_source": request.checkout_source or request.repository,
            "command": list(request.command),
            "exit_code": None,
            "error": f"command exceeded {request.timeout_seconds} seconds",
            "started_at": started_at,
            "finished_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started_monotonic, 6),
            "runtime": {"hostname": socket.gethostname(), "platform": platform.platform(), "python": platform.python_version()},
        }
    except Exception as error:
        completion = {
            "schema": "openresearch.remote-completion.v1",
            "status": "infrastructure_failed",
            "job_id": request.job_id,
            "task_id": request.task_id,
            "request_sha256": payload["request_sha256"],
            "requested_commit": request.commit_sha,
            "checked_out_commit": None,
            "checkout_source": request.checkout_source or request.repository,
            "command": list(request.command),
            "exit_code": None,
            "error": f"{type(error).__name__}: {error}",
            "started_at": started_at,
            "finished_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started_monotonic, 6),
            "runtime": {"hostname": socket.gethostname(), "platform": platform.platform(), "python": platform.python_version()},
        }

    completion["completion_sha256"] = sha256_json(completion)
    (result_dir / "completion.json").write_text(canonical_json(completion) + "\n", encoding="utf-8")
    return {**completion, "remote_result_dir": str(result_dir)}


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: remote_worker.py REQUEST_JSON JOBS_ROOT")
    payload = json.loads(sys.argv[1])
    result = execute(payload, Path(sys.argv[2]))
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
