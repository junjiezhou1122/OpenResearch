from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .contracts import JobRequest, append_ledger, sha256_json
from .remote_worker import execute


class RunnerBackend(Protocol):
    def run(self, request: JobRequest, artifact_root: Path) -> dict[str, Any]: ...


def verify_completion(result: dict[str, Any], request: JobRequest) -> None:
    expected_hash = result.get("completion_sha256")
    unsigned = {key: value for key, value in result.items() if key != "completion_sha256"}
    if expected_hash != sha256_json(unsigned):
        raise RuntimeError("completion hash mismatch")
    if result.get("request_sha256") != request.to_payload()["request_sha256"]:
        raise RuntimeError("completion is bound to a different request")
    if result.get("requested_commit") != request.commit_sha:
        raise RuntimeError("completion requested_commit mismatch")
    checked_out = result.get("checked_out_commit")
    if checked_out is not None and checked_out != request.commit_sha:
        raise RuntimeError("completion checked_out_commit mismatch")
    if result.get("command") != list(request.command):
        raise RuntimeError("completion command mismatch")
    if result.get("checkout_source") != (request.checkout_source or request.repository):
        raise RuntimeError("completion checkout_source mismatch")


@dataclass(frozen=True)
class LocalGitRunner:
    jobs_root: Path

    def run(self, request: JobRequest, artifact_root: Path) -> dict[str, Any]:
        payload = request.to_payload()
        result = execute(payload, self.jobs_root)
        artifact_dir = artifact_root / request.job_id
        artifact_dir.parent.mkdir(parents=True, exist_ok=True)
        source = Path(result.pop("remote_result_dir"))
        shutil.copytree(source, artifact_dir)
        verify_completion(result, request)
        return result


@dataclass(frozen=True)
class SSHGitRunner:
    target: str
    jobs_root: str

    def _validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+", self.target):
            raise ValueError(f"unsafe SSH target: {self.target!r}")
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", self.jobs_root) or ".." in Path(self.jobs_root).parts:
            raise ValueError(f"unsafe remote jobs root: {self.jobs_root!r}")

    def run(self, request: JobRequest, artifact_root: Path) -> dict[str, Any]:
        self._validate()
        payload = request.to_payload()
        worker_source = Path(__file__).with_name("remote_worker.py").read_text(encoding="utf-8")
        contracts_source = Path(__file__).with_name("contracts.py").read_text(encoding="utf-8")
        bootstrap = (
            "import sys,types\n"
            "pkg=types.ModuleType('openresearch'); pkg.__path__=[]; sys.modules['openresearch']=pkg\n"
            "m=types.ModuleType('openresearch.contracts'); sys.modules['openresearch.contracts']=m\n"
            f"exec({contracts_source!r},m.__dict__)\n"
            "w=types.ModuleType('openresearch.remote_worker'); w.__package__='openresearch'; sys.modules['openresearch.remote_worker']=w\n"
            f"exec({worker_source!r},w.__dict__)\n"
            "raise SystemExit(w.main())\n"
        )
        remote_command = "python3 - " + shlex.quote(json.dumps(payload)) + " " + shlex.quote(self.jobs_root)
        process = subprocess.run(
            ["ssh", self.target, remote_command],
            input=bootstrap,
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(f"SSH runner failed with exit {process.returncode}: {process.stderr.strip()}")
        lines = [line for line in process.stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("SSH runner returned no completion payload")
        result = json.loads(lines[-1])
        remote_result_dir = result.pop("remote_result_dir")
        artifact_dir = artifact_root / request.job_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        copy = subprocess.run(
            ["scp", "-q", "-r", f"{self.target}:{remote_result_dir}/.", str(artifact_dir)],
            text=True,
            capture_output=True,
            check=False,
        )
        if copy.returncode != 0:
            raise RuntimeError(f"result collection failed with exit {copy.returncode}: {copy.stderr.strip()}")
        local_completion = json.loads((artifact_dir / "completion.json").read_text(encoding="utf-8"))
        if local_completion != result:
            raise RuntimeError("remote stdout completion does not match collected completion.json")
        verify_completion(result, request)
        return result


@dataclass(frozen=True)
class SSHBundleRunner:
    target: str
    jobs_root: str
    bundles_root: str
    source_repository: Path

    def run(self, request: JobRequest, artifact_root: Path) -> dict[str, Any]:
        ssh_runner = SSHGitRunner(self.target, self.jobs_root)
        ssh_runner._validate()
        expected_source = f"{self.bundles_root.rstrip('/')}/{request.job_id}.bundle"
        if request.checkout_source != expected_source:
            raise ValueError(
                f"bundle checkout source mismatch: expected {expected_source!r}, got {request.checkout_source!r}"
            )
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", self.bundles_root) or ".." in Path(self.bundles_root).parts:
            raise ValueError(f"unsafe remote bundles root: {self.bundles_root!r}")

        with tempfile.TemporaryDirectory(prefix="openresearch-bundle-") as temporary:
            bundle = Path(temporary) / f"{request.job_id}.bundle"
            bundle_process = subprocess.run(
                ["git", "-C", str(self.source_repository), "bundle", "create", str(bundle), "HEAD"],
                text=True,
                capture_output=True,
                check=False,
            )
            if bundle_process.returncode != 0:
                raise RuntimeError(
                    f"git bundle failed with exit {bundle_process.returncode}: {bundle_process.stderr.strip()}"
                )
            verify = subprocess.run(
                ["git", "bundle", "verify", str(bundle)], text=True, capture_output=True, check=False
            )
            if verify.returncode != 0:
                raise RuntimeError(f"git bundle verification failed: {verify.stderr.strip()}")

            create_dir = subprocess.run(
                ["ssh", self.target, "mkdir -p " + shlex.quote(self.bundles_root)],
                text=True,
                capture_output=True,
                check=False,
            )
            if create_dir.returncode != 0:
                raise RuntimeError(f"remote bundle directory creation failed: {create_dir.stderr.strip()}")
            upload = subprocess.run(
                ["scp", "-q", str(bundle), f"{self.target}:{request.checkout_source}"],
                text=True,
                capture_output=True,
                check=False,
            )
            if upload.returncode != 0:
                raise RuntimeError(f"bundle upload failed with exit {upload.returncode}: {upload.stderr.strip()}")

        return ssh_runner.run(request, artifact_root)


def run_recorded(backend: RunnerBackend, request: JobRequest, artifact_root: Path, ledger: Path) -> dict[str, Any]:
    payload = request.to_payload()
    append_ledger(ledger, {"event": "job_dispatched", "job_id": request.job_id, "request": payload})
    try:
        result = backend.run(request, artifact_root)
    except Exception as error:
        append_ledger(
            ledger,
            {"event": "runner_failed", "job_id": request.job_id, "error_type": type(error).__name__, "error": str(error)},
        )
        raise
    append_ledger(ledger, {"event": "job_completed", "job_id": request.job_id, "completion": result})
    return result
