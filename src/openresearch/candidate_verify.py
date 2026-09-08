"""Independent AutoResearch candidate verifier (accept/reject).

Re-derives the verdict mechanically from the evidence bundle and Git state per
the frozen loop protocol (`docs/autoresearch-pycil-loop-protocol.md`). Emits a
machine-readable verdict and appends to the append-only observation ledger.

Usage:
    PYTHONPATH=src python3 -m openresearch.candidate_verify \
        --repo <openresearch-pycil> --state-root <OpenResearch/.openresearch> \
        --job-id <job> --candidate-commit <sha> --incumbent-commit <sha> \
        --incumbent-value 0.59485 --hypothesis "..." --prediction "..." \
        --out <verdict.json path>

Exit 0 = ACCEPT, exit 1 = REJECT (both are verdicts, not errors).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

IMPROVEMENT_MARGIN = 0.001  # ≥0.1 percentage points (frozen §3 A3)
MEMORY_BUDGET = 2000        # frozen guardrail G3
EXPECTED_HOST = "fuxin"
EXPECTED_EVALUATOR_SHA = "2e58757a72cd70432a50d9c97f690958ca3cf68e767a7ccd411942eb20b6c3b6"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(repo: Path, *argv: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *argv], text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(argv)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def parse_tagged_json(stdout: str, tag: str) -> dict | None:
    for line in stdout.splitlines():
        if line.startswith(tag + " "):
            return json.loads(line[len(tag) + 1:])
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--incumbent-commit", required=True)
    parser.add_argument("--incumbent-value", required=True, type=float)
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--prediction", default="")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    checks: list[dict] = []

    def check(name: str, passed: bool, details: dict) -> None:
        checks.append({"check": name, "passed": bool(passed), "details": details})

    job_dir = args.state_root / "artifacts" / args.job_id
    required = ["request.json", "completion.json", "stdout.log", "stderr.log"]
    missing = [n for n in required if not (job_dir / n).is_file()]
    check("bundle_complete", not missing, {"missing": missing})

    metric_value = None
    env_identity = None
    if not missing:
        request = json.loads((job_dir / "request.json").read_text())
        completion = json.loads((job_dir / "completion.json").read_text())
        stdout_text = (job_dir / "stdout.log").read_text(errors="replace")

        # G5 / evidence integrity
        check("exact_commit", completion.get("requested_commit") == args.candidate_commit
              and completion.get("checked_out_commit") == args.candidate_commit,
              {"requested": completion.get("requested_commit"),
               "checked_out": completion.get("checked_out_commit"),
               "candidate": args.candidate_commit})
        check("exit_zero", completion.get("status") == "succeeded"
              and completion.get("exit_code") == 0,
              {"status": completion.get("status"), "exit_code": completion.get("exit_code")})
        check("protocol_id", request.get("protocol_id") == "fml-lite-pycil-loop-v1",
              {"got": request.get("protocol_id")})
        command = list(request.get("command") or [])
        # Two accepted dispatch forms (see ledger amendment 2026-09-08T23:20Z):
        #   frozen:        ["bash", "public_validation/run.sh", "val"]
        #   env-prefixed:  ["env", "CUDA_VISIBLE_DEVICES=<n>", "bash", "public_validation/run.sh", "val"]
        if command[:1] == ["env"] and len(command) >= 2 and command[1].startswith("CUDA_VISIBLE_DEVICES="):
            command_ok = command[2:] == ["bash", "public_validation/run.sh", "val"]
        else:
            command_ok = command == ["bash", "public_validation/run.sh", "val"]
        check("command", command_ok, {"got": command})
        check("host", completion.get("runtime", {}).get("hostname") == EXPECTED_HOST,
              {"got": completion.get("runtime", {}).get("hostname")})

        env_identity = parse_tagged_json(stdout_text, "ENV_IDENTITY_JSON")
        check("env_identity_present", env_identity is not None, {})
        if env_identity:
            check("evaluator_hash", env_identity.get("evaluator_sha256") == EXPECTED_EVALUATOR_SHA,
                  {"got": env_identity.get("evaluator_sha256")})
            check("cuda", env_identity.get("cuda_available") is True, {})

        # A2: metric parseable
        result = parse_tagged_json(stdout_text, "BASELINE_RESULT_JSON")
        metric_ok = (result is not None
                     and result.get("schema") == "openresearch.baseline-result.v1"
                     and result.get("metric") == "avg_incremental_acc_mean"
                     and isinstance(result.get("value"), (int, float))
                     and math.isfinite(float(result["value"]))
                     and 0.0 < float(result["value"]) < 1.0)
        check("metric_parseable", metric_ok, {"result": result})
        if metric_ok:
            metric_value = float(result["value"])

        # G1: diff confined to algorithm.py
        diff_files = git(args.repo, "diff", "--name-only",
                         args.incumbent_commit, args.candidate_commit).splitlines()
        check("diff_confined_to_algorithm", diff_files in ([], ["algorithm.py"]),
              {"diff": diff_files})
        # G1.5: lineage hygiene — the candidate must be a single commit whose
        # parent IS the incumbent commit (prevents stacking unregistered
        # interventions from other candidate branches).
        parent = git(args.repo, "rev-parse", f"{args.candidate_commit}^")
        check("candidate_parent_is_incumbent", parent == args.incumbent_commit,
              {"parent": parent, "incumbent": args.incumbent_commit})

        # G3: memory budget unchanged in candidate tree
        candidate_algo = git(args.repo, "show", f"{args.candidate_commit}:algorithm.py")
        memory_ok = False
        for line in candidate_algo.splitlines():
            stripped = line.split("#")[0].strip().rstrip(",")
            if stripped.startswith('"memory_size"'):
                try:
                    memory_ok = int(stripped.split(":", 1)[1].strip()) == MEMORY_BUDGET
                except ValueError:
                    memory_ok = False
                break
        check("memory_budget_unchanged", memory_ok, {"limit": MEMORY_BUDGET})

        # A3: improvement margin
        improvement_ok = metric_value is not None and (
            metric_value > args.incumbent_value + IMPROVEMENT_MARGIN)
        check("improvement_margin", improvement_ok,
              {"value": metric_value, "incumbent": args.incumbent_value,
               "margin": IMPROVEMENT_MARGIN})

    decision = "ACCEPT" if all(c["passed"] for c in checks) else "REJECT"

    # Full diff text for auditability (empty if bundle failed earlier checks)
    diff_text = ""
    try:
        diff_text = git(args.repo, "diff", args.incumbent_commit, args.candidate_commit)
    except RuntimeError:
        diff_text = "<unavailable>"

    evidence_hasher = hashlib.sha256()
    for name in required:
        path = job_dir / name
        if path.is_file():
            evidence_hasher.update(f"{name}\n".encode())
            evidence_hasher.update(sha256_file(path).encode())
    evidence_hasher.update(f"candidate={args.candidate_commit}\n".encode())
    evidence_hasher.update(f"incumbent={args.incumbent_commit}\n".encode())
    evidence_sha256 = evidence_hasher.hexdigest()

    verdict = {
        "schema": "openresearch.candidate-verdict.v1",
        "protocol": "fml-lite-pycil-loop-v1",
        "job_id": args.job_id,
        "candidate_commit": args.candidate_commit,
        "incumbent_commit": args.incumbent_commit,
        "incumbent_value": args.incumbent_value,
        "candidate_value": metric_value,
        "hypothesis": args.hypothesis,
        "prediction": args.prediction,
        "decision": decision,
        "evidence_sha256": evidence_sha256,
        "diff": diff_text,
        "checks": checks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n")

    ledger = args.state_root / "research" / "pycil-loop" / "observations.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as handle:
        handle.write(json.dumps(verdict, sort_keys=True, separators=(",", ":")) + "\n")

    print(json.dumps({"decision": decision, "candidate_value": metric_value,
                      "failed_checks": [c["check"] for c in checks if not c["passed"]]}))
    return 0 if decision == "ACCEPT" else 1


if __name__ == "__main__":
    sys.exit(main())
