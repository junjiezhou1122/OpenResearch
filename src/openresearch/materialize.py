from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .contracts import canonical_json, utc_now


def _run(argv: list[str], cwd: Path | None = None) -> str:
    env = os.environ.copy()
    # Codex may inject temporary Git config entries for a workspace root. Those
    # entries are not valid after crossing into separately materialized repos;
    # an orphaned GIT_CONFIG_COUNT makes Git fail before it can print the real
    # operation error. Do not forward that process-local config to child repos.
    for key in tuple(env):
        if key == "GIT_CONFIG_COUNT" or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key)
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed with exit {result.returncode}: {argv!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def load_registry(path: Path) -> dict[str, Any]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    if registry.get("schema") != "openresearch.task-registry.v1":
        raise ValueError("unsupported task registry schema")
    return registry


def render_task(task: dict[str, Any], prompt: dict[str, str]) -> str:
    metrics = "\n".join(f"- `{name}`: {direction}" for name, direction in task["metrics"].items())
    edit_paths = "\n".join(f"- `{path}`" for path in task["worker_edit_paths"])
    return f"""# {task['title']}

This is an imported FML-bench-Lite research starter, not a certified baseline.

## Goal

{prompt['task_description']}

## Metrics

{metrics}

## Worker edit surface

{edit_paths}

The worker may commit candidate changes, but it may not create acceptance tags or verdicts. The OpenResearch control plane checks out the exact candidate commit on the runner and an independent evaluator owns final acceptance.
"""


def render_baseline(task: dict[str, Any], fml_commit: str) -> str:
    copied = "\n".join(f"- `{item['source']}` -> `{item['destination']}`" for item in task["starter_files"]) or "- No FML adapter file is copied; the task starts from the frozen upstream tree."
    return f"""# Baseline provenance

Status: **imported starter; not certified**

- Upstream: `{task['upstream_url']}`
- Upstream commit: `{task['upstream_commit']}`
- FML-bench source commit: `{fml_commit}`
- FML task: `{task['fml_task']}`
- Materialized at: `{utc_now()}`

Imported FML worker-facing files:

{copied}

Do not create `baseline/v1` until the frozen evaluator has executed the starter on the target server, preserved exit code and artifacts, and repeated enough runs to characterize variance.
"""


def render_agents(task: dict[str, Any]) -> str:
    paths = ", ".join(f"`{path}`" for path in task["worker_edit_paths"])
    return f"""# AGENTS.md

- Fail fast. Never append `|| true`, `; true`, or equivalent error suppression to experiment commands.
- Fix root causes and preserve stdout, stderr, exit code, commit SHA, and runtime identity for every run.
- The default worker edit surface is: {paths}.
- Do not modify hidden-test data, evaluator code, result ledgers, acceptance verdicts, or Git tags.
- A validation improvement is a proxy observation, not proof of holdout success.
- Update TASK.md or BASELINE.md whenever the task contract or baseline changes.
- Create a branch before broad refactors or experimental changes.
"""


def materialize_task(
    task: dict[str, Any],
    destination_root: Path,
    fml_source: Path,
    fml_commit: str,
    owner: str | None,
    visibility: str,
) -> Path:
    target = destination_root / task["repo"]
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")
    destination_root.mkdir(parents=True, exist_ok=True)

    _run(["git", "clone", "--filter=blob:none", "--no-checkout", task["upstream_url"], str(target)])
    try:
        _run(["git", "fetch", "--depth", "1", "origin", task["upstream_commit"]], cwd=target)
        _run(["git", "checkout", "--detach", task["upstream_commit"]], cwd=target)
        actual = _run(["git", "rev-parse", "HEAD"], cwd=target)
        if actual != task["upstream_commit"]:
            raise RuntimeError(f"upstream checkout mismatch for {task['id']}: {actual}")
        upstream_tree = _run(["git", "rev-parse", "HEAD^{tree}"], cwd=target)
        snapshot = _run(
            [
                "git",
                "commit-tree",
                upstream_tree,
                "-m",
                f"chore: import upstream snapshot {actual}",
            ],
            cwd=target,
        )
        # The dedicated research repository starts with a self-contained root
        # snapshot. It records upstream provenance without trying to push a
        # filtered/shallow object graph into a different remote.
        _run(["git", "branch", "-f", "main", snapshot], cwd=target)
        _run(["git", "switch", "main"], cwd=target)
        _run(["git", "tag", f"import/upstream-{actual[:12]}", snapshot], cwd=target)
        _run(["git", "remote", "rename", "origin", "upstream"], cwd=target)

        task_source = fml_source / "ml_tasks" / task["fml_task"]
        prompt = json.loads((task_source / "prompt.json").read_text(encoding="utf-8"))
        for item in task["starter_files"]:
            source = task_source / item["source"]
            destination = target / item["destination"]
            if not source.is_file():
                raise FileNotFoundError(f"missing FML starter file: {source}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        metadata_dir = target / ".openresearch"
        metadata_dir.mkdir()
        metadata = {
            "schema": "openresearch.task.v1",
            **{key: value for key, value in task.items() if key != "starter_files"},
            "fml_bench_commit": fml_commit,
            "status": "imported_starter",
        }
        (metadata_dir / "task.json").write_text(canonical_json(metadata) + "\n", encoding="utf-8")
        (target / "TASK.md").write_text(render_task(task, prompt), encoding="utf-8")
        (target / "BASELINE.md").write_text(render_baseline(task, fml_commit), encoding="utf-8")
        (target / "AGENTS.md").write_text(render_agents(task), encoding="utf-8")
        ignore = target / ".gitignore"
        existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        additions = "\n# OpenResearch local artifacts\n.openresearch/runs/\nartifacts/\ndata/\ndatasets/\n"
        if ".openresearch/runs/" not in existing:
            ignore.write_text(existing.rstrip() + additions, encoding="utf-8")

        _run(["git", "add", ".openresearch/task.json", "TASK.md", "BASELINE.md", "AGENTS.md", ".gitignore"], cwd=target)
        for item in task["starter_files"]:
            _run(["git", "add", item["destination"]], cwd=target)
        _run(["git", "commit", "-m", "chore: materialize FML-bench-Lite research starter"], cwd=target)

        if owner:
            full_name = f"{owner}/{task['repo']}"
            _run(["gh", "repo", "create", full_name, f"--{visibility}", "--description", task["description"]])
            _run(["git", "remote", "add", "origin", f"https://github.com/{full_name}.git"], cwd=target)
            _run(["git", "push", "-u", "origin", "main"], cwd=target)
            _run(["git", "push", "origin", f"import/upstream-{actual[:12]}"], cwd=target)
        return target
    except Exception:
        print(f"materialization failed; partial checkout preserved for diagnosis: {target}")
        raise
