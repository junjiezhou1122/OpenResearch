#!/bin/bash
# Dispatches the frozen FML-bench-Lite PyCIL baseline runs sequentially through
# the OpenResearch ssh-bundle runner. Each run is an independent exact-SHA job.
#
# Fail fast: the loop stops at the first non-zero runner exit; the failed run
# remains in the append-only ledger and artifacts. No retries, no || true.
set -euo pipefail
cd "$(dirname "$0")/.."

N_RUNS="${N_RUNS:-3}"
SPLIT="${SPLIT:-val}"
REPO=../fml-lite/openresearch-pycil
COMMIT="$(git -C "$REPO" rev-parse HEAD)"

echo "=== certifying commit: $COMMIT (split=$SPLIT, runs=$N_RUNS) ==="
for i in $(seq 1 "$N_RUNS"); do
  echo "=== baseline run $i of $N_RUNS: $(date -u +%FT%TZ) ==="
  PYTHONPATH=src python3 -m openresearch.cli run \
    --task pycil \
    --repo "$REPO" \
    --repo-url https://github.com/junjiezhou1122/openresearch-pycil.git \
    --backend ssh-bundle \
    --ssh-target hangzhou_server \
    --remote-jobs-root /home/zhoujunjie/openresearch-jobs \
    --remote-bundles-root /home/zhoujunjie/openresearch-bundles \
    --protocol fml-lite-pycil-baseline-v1 \
    --timeout 7200 \
    -- bash public_validation/run.sh "$SPLIT"
done
echo "=== ALL_RUNS_DONE: $(date -u +%FT%TZ) ==="
