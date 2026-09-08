from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from .contracts import JobRequest, canonical_json, utc_now
from .materialize import load_registry, materialize_task
from .runner import LocalGitRunner, SSHBundleRunner, SSHGitRunner, run_recorded


def _full_sha(repository: Path) -> str:
    env = os.environ.copy()
    for key in tuple(env):
        if key == "GIT_CONFIG_COUNT" or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True, capture_output=True, check=True, env=env
    )
    sha = result.stdout.strip()
    if len(sha) != 40:
        raise RuntimeError(f"unexpected Git SHA: {sha!r}")
    return sha


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openresearch")
    subparsers = parser.add_subparsers(dest="action", required=True)

    run = subparsers.add_parser("run", help="run an exact candidate commit locally or through SSH")
    run.add_argument("--task", required=True)
    run.add_argument("--repo", type=Path, required=True)
    run.add_argument("--repo-url")
    run.add_argument("--protocol", default="fml-lite-dev-v1")
    run.add_argument("--timeout", type=int, default=3600)
    run.add_argument("--backend", choices=("local", "ssh", "ssh-bundle"), required=True)
    run.add_argument("--ssh-target")
    run.add_argument("--remote-jobs-root", default="/tmp/openresearch-jobs")
    run.add_argument("--remote-bundles-root", default="/tmp/openresearch-bundles")
    run.add_argument("--state-root", type=Path, default=Path(".openresearch"))
    run.add_argument("command", nargs=argparse.REMAINDER)

    materialize = subparsers.add_parser("materialize", help="create one frozen FML-Lite task repository")
    materialize.add_argument("--registry", type=Path, default=Path("task-registry/fml-lite.json"))
    materialize.add_argument("--task", required=True)
    materialize.add_argument("--destination-root", type=Path, required=True)
    materialize.add_argument("--fml-source", type=Path, required=True)
    materialize.add_argument("--github-owner")
    materialize.add_argument("--visibility", choices=("public", "private"), default="public")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "materialize":
        registry = load_registry(args.registry)
        tasks = {task["id"]: task for task in registry["tasks"]}
        if args.task not in tasks:
            raise SystemExit(f"unknown task {args.task!r}; choose from: {', '.join(tasks)}")
        path = materialize_task(
            tasks[args.task],
            args.destination_root,
            args.fml_source,
            registry["fml_bench_commit"],
            args.github_owner,
            args.visibility,
        )
        print(path)
        return 0

    if not args.command:
        raise SystemExit("a command is required after --")
    command = args.command[1:] if args.command[0] == "--" else args.command
    repository = args.repo.resolve()
    job_id = f"{args.task}-{uuid.uuid4().hex[:12]}"
    checkout_source = None
    if args.backend == "ssh-bundle":
        checkout_source = f"{args.remote_bundles_root.rstrip('/')}/{job_id}.bundle"
    request = JobRequest(
        job_id=job_id,
        task_id=args.task,
        repository=args.repo_url or str(repository),
        commit_sha=_full_sha(repository),
        command=tuple(command),
        protocol_id=args.protocol,
        timeout_seconds=args.timeout,
        created_at=utc_now(),
        checkout_source=checkout_source,
    )
    state_root = args.state_root.resolve()
    if args.backend == "local":
        backend = LocalGitRunner(state_root / "local-jobs")
    elif args.backend == "ssh":
        if not args.ssh_target:
            raise SystemExit("--ssh-target is required for the ssh backend")
        if not args.repo_url:
            raise SystemExit("--repo-url is required for the ssh backend")
        backend = SSHGitRunner(args.ssh_target, args.remote_jobs_root)
    else:
        if not args.ssh_target:
            raise SystemExit("--ssh-target is required for the ssh-bundle backend")
        if not args.repo_url:
            raise SystemExit("--repo-url is required for the ssh-bundle backend")
        backend = SSHBundleRunner(
            args.ssh_target,
            args.remote_jobs_root,
            args.remote_bundles_root,
            repository,
        )
    result = run_recorded(backend, request, state_root / "artifacts", state_root / "ledger.jsonl")
    print(canonical_json(result))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
