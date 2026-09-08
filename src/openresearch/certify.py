"""Independent baseline-certification verifier.

Mechanically re-derives the verdict from the evidence bundle on disk. It does
not consult the worker, the runner exit status, or the goal model: every check
re-reads request.json / completion.json / stdout.log / stderr.log and the task
repository Git state.

Usage:
    python -m openresearch.certify \
        --task pycil \
        --repo /path/to/openresearch-pycil \
        --state-root /path/to/OpenResearch/.openresearch \
        --job-ids id1,id2,id3 \
        --commit <certified-commit-sha> \
        --evaluator-sha256 <pinned-sha> \
        --allowed-harness-paths public_validation/run.sh,protocol/baseline-protocol.md \
        --out <verdict.json path>

Exit code is 0 only for a PASS verdict. The verdict is hash-bound to the
evidence it examined (evidence_sha256 covers every bundle file plus the Git
diff against the parent commit).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

SPREAD_LIMIT_PERCENTAGE_POINTS = 1.0
MIN_RUNS = 3


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(repository: Path, *argv: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *argv], text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(argv)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def parse_env_identity(stdout: str) -> dict | None:
    for line in stdout.splitlines():
        if line.startswith("ENV_IDENTITY_JSON "):
            return json.loads(line[len("ENV_IDENTITY_JSON "):])
    return None


def parse_result(stdout: str) -> dict | None:
    for line in stdout.splitlines():
        if line.startswith("BASELINE_RESULT_JSON "):
            return json.loads(line[len("BASELINE_RESULT_JSON "):])
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--job-ids", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--evaluator-sha256", required=True)
    parser.add_argument("--allowed-harness-paths", default="")
    parser.add_argument("--min-runs", type=int, default=MIN_RUNS)
    parser.add_argument("--spread-limit", type=float, default=SPREAD_LIMIT_PERCENTAGE_POINTS)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    checks: list[dict] = []

    def check(name: str, passed: bool, details: dict) -> None:
        checks.append({"check": name, "passed": bool(passed), "details": details})

    job_ids = [job.strip() for job in args.job_ids.split(",") if job.strip()]
    artifacts_root = args.state_root / "artifacts"

    # ---- check 0: commit exists and is a descendant of its parent in the repo ----
    commit = git(args.repo, "rev-parse", f"{args.commit}^{{commit}}")
    parent = git(args.repo, "rev-parse", f"{args.commit}^")
    check("commit_exists", bool(commit), {"commit": commit})
    check("parent_resolvable", bool(parent), {"parent": parent})

    # ---- per-run bundle checks ----
    values: list[float] = []
    env_identities: list[dict] = []
    bundle_hashes: list[str] = []
    per_run_details: list[dict] = []
    for job_id in job_ids:
        job_dir = artifacts_root / job_id
        required = ["request.json", "completion.json", "stdout.log", "stderr.log"]
        missing = [name for name in required if not (job_dir / name).is_file()]
        check(f"bundle_complete:{job_id}", not missing, {"missing": missing})

        request = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
        completion = json.loads((job_dir / "completion.json").read_text(encoding="utf-8"))

        # request/completion hash integrity
        expected_request_hash = request.get("request_sha256")
        unsigned_request = {k: v for k, v in request.items() if k != "request_sha256"}
        actual_request_hash = hashlib.sha256(
            json.dumps(unsigned_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        check(f"request_hash:{job_id}", expected_request_hash == actual_request_hash, {
            "expected": expected_request_hash, "actual": actual_request_hash,
        })

        expected_completion_hash = completion.get("completion_sha256")
        unsigned_completion = {k: v for k, v in completion.items() if k != "completion_sha256"}
        actual_completion_hash = hashlib.sha256(
            json.dumps(unsigned_completion, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        check(f"completion_hash:{job_id}", expected_completion_hash == actual_completion_hash, {
            "expected": expected_completion_hash, "actual": actual_completion_hash,
        })

        # completion bound to request
        check(f"completion_bound_to_request:{job_id}",
              completion.get("request_sha256") == request.get("request_sha256"), {})

        # exact commit, status, exit code, command
        check(f"exact_commit:{job_id}",
              completion.get("requested_commit") == args.commit
              and completion.get("checked_out_commit") == args.commit,
              {"requested": completion.get("requested_commit"),
               "checked_out": completion.get("checked_out_commit")})
        check(f"exit_code_zero:{job_id}",
              completion.get("status") == "succeeded" and completion.get("exit_code") == 0,
              {"status": completion.get("status"), "exit_code": completion.get("exit_code")})
        check(f"task_id:{job_id}", completion.get("task_id") == args.task, {})
        check(f"checkout_via_bundle:{job_id}",
              str(completion.get("checkout_source", "")).endswith(".bundle"),
              {"checkout_source": completion.get("checkout_source")})

        # metric parseability and finiteness
        stdout_text = (job_dir / "stdout.log").read_text(encoding="utf-8", errors="replace")
        result = parse_result(stdout_text)
        metric_ok = (
            result is not None
            and result.get("schema") == "openresearch.baseline-result.v1"
            and result.get("metric") == "avg_incremental_acc_mean"
            and isinstance(result.get("value"), (int, float))
            and math.isfinite(float(result["value"]))
            and 0.0 < float(result["value"]) < 1.0
        )
        check(f"metric_parseable:{job_id}", metric_ok, {"result": result})
        if metric_ok:
            values.append(float(result["value"]))

        env_identity = parse_env_identity(stdout_text)
        check(f"env_identity_present:{job_id}", env_identity is not None, {})
        if env_identity is not None:
            env_identities.append(env_identity)
            check(f"env_evaluator_hash:{job_id}",
                  env_identity.get("evaluator_sha256") == args.evaluator_sha256,
                  {"got": env_identity.get("evaluator_sha256")})
            check(f"env_cuda:{job_id}",
                  env_identity.get("cuda_available") is True
                  and int(env_identity.get("gpu_count") or 0) >= 1, {})

        bundle_hashes.append(sha256_file(job_dir / "completion.json"))
        per_run_details.append({
            "job_id": job_id,
            "metric_value": result.get("value") if result else None,
            "exit_code": completion.get("exit_code"),
            "duration_seconds": completion.get("duration_seconds"),
            "started_at": completion.get("started_at"),
            "finished_at": completion.get("finished_at"),
        })

    # ---- run-count, distinct-jobs, and variance checks ----
    check("run_count", len(job_ids) >= args.min_runs, {"runs": len(job_ids), "required": args.min_runs})
    check("distinct_job_ids", len(set(job_ids)) == len(job_ids), {})
    spread_ok = False
    spread = None
    if len(values) >= 2:
        spread = max(values) - min(values)
        spread_ok = spread <= args.spread_limit / 100.0
    check("metric_spread_within_limit", spread_ok,
          {"spread": spread, "limit_percentage_points": args.spread_limit, "values": values})
    check("env_identity_consistent", len({json.dumps(e, sort_keys=True) for e in env_identities}) == 1,
          {"distinct_env_identities": len({json.dumps(e, sort_keys=True) for e in env_identities})})

    # ---- allowed-diff check against parent commit ----
    diff = git(args.repo, "diff", "--name-only", parent, commit).splitlines()
    allowed = [p for p in args.allowed_harness_paths.split(",") if p.strip()]
    disallowed = sorted(set(diff) - set(allowed))
    check("allowed_diff_only", not disallowed, {"diff": diff, "disallowed": disallowed})

    # ---- evidence bundle hash ----
    evidence_hasher = hashlib.sha256()
    for job_id in job_ids:
        job_dir = artifacts_root / job_id
        for name in required:
            path = job_dir / name
            if path.is_file():
                evidence_hasher.update(f"{job_id}/{name}\n".encode())
                evidence_hasher.update(sha256_file(path).encode())
    evidence_hasher.update(f"commit={commit}\n".encode())
    evidence_hasher.update(f"diff={git(args.repo, 'diff', parent, commit)}".encode())
    evidence_sha256 = evidence_hasher.hexdigest()

    decision = "PASS" if all(item["passed"] for item in checks) else "FAIL"
    verdict = {
        "schema": "openresearch.acceptance.v1",
        "task": args.task,
        "protocol": "fml-lite-pycil-baseline-v1",
        "certified_commit": commit,
        "parent_commit": parent,
        "runs": per_run_details,
        "metric_values": values,
        "metric_mean": sum(values) / len(values) if values else None,
        "evidence_sha256": evidence_sha256,
        "decision": decision,
        "checks": checks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"decision": decision, "evidence_sha256": evidence_sha256,
                      "failed_checks": [c["check"] for c in checks if not c["passed"]]}))
    return 0 if decision == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
