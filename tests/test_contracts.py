import json
import subprocess
from pathlib import Path

import pytest

from openresearch.contracts import JobRequest, utc_now
from openresearch.materialize import materialize_task
from openresearch.runner import LocalGitRunner, run_recorded, verify_completion


def git(argv: list[str], cwd: Path) -> str:
    return subprocess.run(["git", *argv], cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def make_repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    git(["init", "-b", "main"], repo)
    git(["config", "user.name", "OpenResearch Test"], repo)
    git(["config", "user.email", "test@openresearch.invalid"], repo)
    (repo / "value.txt").write_text("first\n", encoding="utf-8")
    git(["add", "value.txt"], repo)
    git(["commit", "-m", "first"], repo)
    first = git(["rev-parse", "HEAD"], repo)
    (repo / "value.txt").write_text("second\n", encoding="utf-8")
    git(["commit", "-am", "second"], repo)
    return repo, first


def request(repo: Path, sha: str, job_id: str, command: tuple[str, ...]) -> JobRequest:
    return JobRequest(
        job_id=job_id,
        task_id="fixture",
        repository=str(repo),
        commit_sha=sha,
        command=command,
        protocol_id="test-v1",
        timeout_seconds=10,
        created_at=utc_now(),
    )


def test_runner_uses_exact_sha_even_when_branch_moved(tmp_path: Path) -> None:
    repo, first = make_repository(tmp_path)
    state = tmp_path / "state"
    result = run_recorded(
        LocalGitRunner(state / "jobs"),
        request(repo, first, "exact-sha", ("sh", "-c", "test \"$(cat value.txt)\" = first")),
        state / "artifacts",
        state / "ledger.jsonl",
    )
    assert result["status"] == "succeeded"
    assert result["checked_out_commit"] == first
    events = [json.loads(line)["event"] for line in (state / "ledger.jsonl").read_text().splitlines()]
    assert events == ["job_dispatched", "job_completed"]


def test_candidate_failure_is_not_swallowed(tmp_path: Path) -> None:
    repo, first = make_repository(tmp_path)
    state = tmp_path / "state"
    result = run_recorded(
        LocalGitRunner(state / "jobs"),
        request(repo, first, "failing-command", ("sh", "-c", "echo boom >&2; exit 17")),
        state / "artifacts",
        state / "ledger.jsonl",
    )
    assert result["status"] == "failed"
    assert result["exit_code"] == 17
    assert "boom" in (state / "artifacts" / "failing-command" / "stderr.log").read_text()


def test_request_hash_detects_tampering(tmp_path: Path) -> None:
    repo, first = make_repository(tmp_path)
    payload = request(repo, first, "tamper", ("true",)).to_payload()
    payload["commit_sha"] = "0" * 40
    with pytest.raises(ValueError, match="request hash mismatch"):
        JobRequest.from_payload(payload)


def test_completion_hash_detects_tampering(tmp_path: Path) -> None:
    repo, first = make_repository(tmp_path)
    state = tmp_path / "state"
    job_request = request(repo, first, "completion-tamper", ("true",))
    result = run_recorded(
        LocalGitRunner(state / "jobs"), job_request, state / "artifacts", state / "ledger.jsonl"
    )
    result["exit_code"] = 99
    with pytest.raises(RuntimeError, match="completion hash mismatch"):
        verify_completion(result, job_request)


def test_materializer_freezes_upstream_and_does_not_certify_baseline(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(["init", "-b", "main"], upstream)
    git(["config", "user.name", "OpenResearch Test"], upstream)
    git(["config", "user.email", "test@openresearch.invalid"], upstream)
    (upstream / "source.py").write_text("UPSTREAM = True\n", encoding="utf-8")
    git(["add", "source.py"], upstream)
    git(["commit", "-m", "upstream"], upstream)
    upstream_sha = git(["rev-parse", "HEAD"], upstream)

    fml = tmp_path / "fml"
    task_source = fml / "ml_tasks" / "Fixture_task"
    task_source.mkdir(parents=True)
    (task_source / "prompt.json").write_text(
        json.dumps({"system": "fixture", "task_description": "Improve the fixture."}), encoding="utf-8"
    )
    (task_source / "algorithm.py").write_text("VALUE = 1\n", encoding="utf-8")
    task = {
        "id": "fixture",
        "title": "Fixture",
        "repo": "openresearch-fixture",
        "description": "fixture",
        "fml_task": "Fixture_task",
        "upstream_url": str(upstream),
        "upstream_commit": upstream_sha,
        "metrics": {"score": "higher"},
        "worker_edit_paths": ["algorithm.py"],
        "starter_files": [{"source": "algorithm.py", "destination": "algorithm.py"}],
    }
    target = materialize_task(task, tmp_path / "tasks", fml, "f" * 40, None, "public")
    metadata = json.loads((target / ".openresearch" / "task.json").read_text())
    assert metadata["status"] == "imported_starter"
    assert (target / "algorithm.py").read_text() == "VALUE = 1\n"
    import_tag = "import/upstream-" + upstream_sha[:12]
    tagged_commit = git(["rev-parse", import_tag], target)
    assert (target / "source.py").read_text() == "UPSTREAM = True\n"
    assert len(git(["rev-list", "--parents", "-n", "1", tagged_commit], target).split()) == 1
    assert "baseline/v1" not in git(["tag", "--list"], target).splitlines()
