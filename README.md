# OpenResearch

OpenResearch is the control plane for long-running AutoResearch loops. Research code lives in dedicated task repositories; this repository binds every run to an immutable commit, sends it to a compute backend, and returns auditable evidence to the local loop.

```text
local task repo: edit → commit → push
                         ↓ exact SHA
OpenResearch: request hash → runner backend → append-only ledger
                         ↓
compute server: fresh checkout → execute → preserve evidence
                         ↓
local artifacts: stdout/stderr + exit code + runtime + completion hash
```

## FML-bench-Lite task repositories

- [openresearch-pycil](https://github.com/junjiezhou1122/openresearch-pycil)
- [openresearch-usb](https://github.com/junjiezhou1122/openresearch-usb)
- [openresearch-coloredmnist](https://github.com/junjiezhou1122/openresearch-coloredmnist)
- [openresearch-officehome](https://github.com/junjiezhou1122/openresearch-officehome)
- [openresearch-openood](https://github.com/junjiezhou1122/openresearch-openood)
- [openresearch-opacus](https://github.com/junjiezhou1122/openresearch-opacus)
- [openresearch-privacymeter](https://github.com/junjiezhou1122/openresearch-privacymeter)
- [openresearch-art](https://github.com/junjiezhou1122/openresearch-art)

Each repository starts from a frozen upstream snapshot plus its worker-facing FML adapter. Its current status is `imported_starter`; no repository is labeled `baseline/v1` until a separate evaluator certifies it on the target hardware.

## Run a candidate

Install the local CLI:

```bash
python3 -m pip install -e .
```

Use direct remote fetch when the compute server can reach GitHub:

```bash
openresearch run \
  --task pycil \
  --repo ../fml-lite/openresearch-pycil \
  --repo-url https://github.com/junjiezhou1122/openresearch-pycil.git \
  --backend ssh \
  --ssh-target YOUR_SSH_ALIAS \
  -- bash -lc './public_validation/run.sh'
```

Use the explicit bundle transport when the server cannot reach GitHub reliably:

```bash
openresearch run \
  --task pycil \
  --repo ../fml-lite/openresearch-pycil \
  --repo-url https://github.com/junjiezhou1122/openresearch-pycil.git \
  --backend ssh-bundle \
  --ssh-target YOUR_SSH_ALIAS \
  -- bash -lc './public_validation/run.sh'
```

Before SSH dispatch, the runner verifies that the exact candidate SHA is published at the canonical remote. It never identifies a run by a moving branch name and never uses `git pull` in a shared working tree. See [the remote runner contract](docs/remote-runner-contract.md) and [the FML-Lite repository lifecycle](docs/fml-lite-repository-lifecycle.md).

## Current boundary

The transport and evidence loop is implemented. The frozen task environments, datasets, public-validation entrypoints, hidden evaluator, acceptance schema implementation, and baseline measurements remain to be certified. A successful runner completion is an observation; it is not an acceptance verdict.
