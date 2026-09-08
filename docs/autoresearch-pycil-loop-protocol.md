# AutoResearch Loop Protocol — PyCIL (frozen)

Protocol ID: `fml-lite-pycil-loop-v1`
Frozen at: 2026-09-08, BEFORE any candidate measurement.
Baseline (incumbent v1): `baseline/v1` = commit `15c78044b94d57c0cfd84eedc6b547b3c87b99d9`,
val metric `avg_incremental_acc_mean` = 0.59485 (certified, 3 runs, spread 0.0pp).
Test-split reference for holdout: baseline test value is measured only at holdout
verification (§6); it was deliberately never evaluated during development.

## 1. Unit of work

One candidate = one falsifiable hypothesis + one bounded intervention confined
to `algorithm.py` (the task's worker edit surface). Each candidate:

1. hypothesis and prediction recorded in the observation ledger BEFORE dispatch;
2. commit on a `research/<candidate-id>` branch in `openresearch-pycil`, pushed;
3. dispatched by OpenResearch via `ssh-bundle` with exact SHA, command
   `bash public_validation/run.sh val`, timeout 7200 s;
4. observation parsed from the evidence bundle by the independent verifier;
5. accept/reject verdict appended to the observation ledger (append-only).

## 2. Guardrails (verifier-enforced, frozen)

- G1: diff `incumbent..candidate` touches `algorithm.py` only.
- G2: `BaseLearner` interface intact — the candidate diff must not alter the
  signatures or semantics of `incremental_train(data_manager)`, `eval_task()`,
  `after_task()` (evaluator imports and calls them; signature changes break the
  run and fail exit-code check, and the verifier additionally rejects diff hunks
  touching `models/base.py` or the evaluator path).
- G3: memory budget unchanged: `memory_size` stays 2000.
- G4: dataset/split/evaluator untouched (no changes to `public_validation/`,
  `protocol/`, `utils/`, `models/`, `trainer.py`, `main.py`).
- G5: run must be a genuine measurement: exit 0, exact-commit checkout, runtime
  host `fuxin`, evaluator sha256 `2e58757a…b6c3b6` verified by `run.sh`.

## 3. Accept criteria (frozen)

A candidate is ACCEPTED iff ALL hold:

- A1: G1–G5 pass;
- A2: metric parseable, finite, in (0, 1);
- A3: `value > incumbent_value + 0.001` (≥0.1 percentage points above the
  incumbent's recorded val value; run noise observed at baseline was 0.0pp).

Only the verifier (separate process in the OpenResearch repository) may emit an
ACCEPT verdict. The worker/goal model may not self-accept.

## 4. Promotion

- First accepted candidate → tag `incumbent/v2` on the candidate commit;
  further accepted candidates → `incumbent/v3`, …
- The incumbent pointer (ledger + `task.json` `incumbent_commit`) is updated
  only after an ACCEPT verdict.
- Rejected candidates are never merged to main and never tagged.

## 5. Budget (frozen)

- Max 20 candidate val runs.
- Per-run timeout 7200 s; expected cost ≈20–30 min/run on RTX 3090.
- Budget exhaustion with zero accepted candidates: holdout verification runs on
  `baseline/v1` itself; the loop ends with baseline retained as incumbent.

## 6. Holdout verification (end of loop)

- Final incumbent and `baseline/v1` each run once with `--split test` through
  the same runner (exact-SHA, fresh checkout).
- Final promotion to the last `incumbent/vN` requires
  `incumbent_test_value > baseline_test_value`. A val improvement that does not
  hold on the test split is reported honestly: the incumbent tag stays but the
  final report must state the holdout outcome.
- Test-split values are never used for candidate selection during the loop.

## 7. Honest-reporting rules

- No `|| true`, no silent retries, no exception swallowing: a failed dispatch is
  an observation with the failure evidence preserved.
- Acceptance criteria and budget are frozen; changes require a new protocol ID
  and invalidate prior verdicts.
- The observation ledger is append-only; every entry carries the evidence
  bundle path and hashes.
