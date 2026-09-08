# Local edit → remote execution contract

The research loop is intentionally split into two authorities:

```text
task repository on laptop
  edit → commit → push
             ↓ exact 40-character SHA
OpenResearch control plane
  hash request → append dispatch event → invoke RunnerBackend
             ↓
fresh directory on compute server
  clone → fetch SHA → detached checkout → run argv
             ↓
result bundle
  request + stdout + stderr + exit code + runtime + completion hash
             ↓
local append-only ledger and artifacts
```

The server does not run `git pull`. A mutable branch name can move between dispatch and execution, so the runner checks out the exact submitted commit in a fresh job directory. Reusing a job directory is an error.

Two explicit SSH transports implement that invariant:

- `ssh`: the server clones/fetches the canonical Git remote itself;
- `ssh-bundle`: the control plane verifies and uploads a Git bundle containing the local `HEAD`, then the server clones that bundle.

`ssh-bundle` is not an automatic fallback. The selected transport and checkout source are hash-bound in the request so a GitHub networking problem cannot silently change how a run was sourced.

## What this layer does

- abstracts local and SSH execution behind the same `RunnerBackend` contract;
- binds a run to task, protocol, repository, commit, command and timeout;
- refuses SSH dispatch until the exact candidate commit is published at a canonical remote ref;
- preserves command failures instead of converting them into success;
- returns the evidence bundle to the local control plane;
- appends dispatch, completion and infrastructure-failure events.

## What this layer does not prove

Successful execution is an observation, not acceptance. The runner does not decide whether a metric improvement is scientifically valid. A separate evaluator must own frozen requirements, hidden data, metric calculation and the final machine verdict. Human acceptance may follow for high-risk publication or deployment.

## CLI examples

Local transport check:

```bash
openresearch run \
  --task pycil \
  --repo ../fml-lite/openresearch-pycil \
  --backend local \
  -- python -m pytest
```

SSH runner:

```bash
openresearch run \
  --task pycil \
  --repo ../fml-lite/openresearch-pycil \
  --repo-url https://github.com/junjiezhou1122/openresearch-pycil.git \
  --backend ssh \
  --ssh-target YOUR_SSH_ALIAS \
  --remote-jobs-root /srv/openresearch/jobs \
  -- bash -lc './public_validation/run.sh'
```

SSH bundle runner for a compute server without reliable GitHub access:

```bash
openresearch run \
  --task pycil \
  --repo ../fml-lite/openresearch-pycil \
  --repo-url https://github.com/junjiezhou1122/openresearch-pycil.git \
  --backend ssh-bundle \
  --ssh-target YOUR_SSH_ALIAS \
  --remote-jobs-root /srv/openresearch/jobs \
  --remote-bundles-root /srv/openresearch/bundles \
  -- bash -lc './public_validation/run.sh'
```

The SSH command blocks until the run finishes and then copies the result bundle into `.openresearch/artifacts/<job-id>/`. Async scheduling can be added as another backend without changing task repositories or request semantics.
