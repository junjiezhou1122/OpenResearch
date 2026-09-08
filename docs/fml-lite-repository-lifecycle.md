# FML-bench-Lite 研究仓库拓扑与生命周期

> 状态：控制平面与 8 个任务仓库已物化；baseline 尚未认证
> 日期：2026-09-08

## 1. 决策

`OpenResearch` 的职责是 AutoResearch harness / control plane，不直接充当某一个科学任务的实验代码库。

FML-bench-Lite 的 8 个任务应分别物化为 8 个长期研究仓库。每个任务仓库从一个冻结、可复现并由外部 evaluator 测量过的 baseline 开始；AutoResearch 在其上持续提出候选、执行实验、保留证据并晋升新的 incumbent。

```text
FML-bench-Lite task definition
        ↓ materialize
Dedicated research repository
        ↓ certify baseline
Immutable baseline tag
        ↓ AutoResearch iterations
Candidate commits / branches
        ↓ external verifier
Reject or promote
        ↓
Accepted incumbent history
```

## 2. 为什么是一任务一仓库

一任务一仓库不是 evaluator 隔离的充分条件，但它能提供清晰的长期研究边界：

- 一个仓库对应一个 goal、数据协议、metric 和 intervention surface；
- baseline、候选和 incumbent 形成可读的 Git 历史；
- 任务依赖、运行环境和实验文档不会与其他任务一起漂移；
- 每个任务可以独立分支、回滚、发布和分配算力；
- 后续加入新的 Agent 或研究策略时，可以在相同 baseline 上公平重放；
- 某个任务成长为独立研究项目时，无需再从中央 monorepo 拆出历史。

中央 benchmark 项目使用 monorepo/workspace 是为了统一分发和跑榜；长期 AutoResearch 项目需要保存每个研究对象的演化史，两者的打包目标不同。

## 3. 仓库职责

### 3.1 OpenResearch：控制平面

```text
OpenResearch/
├── task-registry/          # 任务仓库、baseline、protocol 和 evaluator 身份
├── orchestration/          # 启动、恢复、预算、stall detection
├── worker-contracts/       # Worker 可见信息和可修改边界
├── verifier-clients/       # 调用独立 evaluator，不包含隐藏答案
├── schemas/                # manifest、observation、candidate、verdict
├── reporting/              # 跨任务汇总，不参与任务内选择
└── docs/                   # 架构、协议和决策记录
```

OpenResearch 不拥有某个任务的模型实现，也不应把 8 个实验的 working tree 混在自身历史中。

### 3.2 八个任务仓库：研究平面

建议的一任务一仓库映射：

| FML-Lite 任务 | 建议研究仓库标识 | 上游来源 |
|---|---|---|
| Continual Learning / PyCIL | `openresearch-pycil` | PyCIL |
| Data Efficiency / USB | `openresearch-usb` | Semi-supervised-learning / USB |
| Generalization / ColoredMNIST | `openresearch-coloredmnist` | DomainBed |
| Generalization / OfficeHome | `openresearch-officehome` | DomainBed |
| Robustness / OpenOOD | `openresearch-openood` | OpenOOD |
| Privacy / Opacus | `openresearch-opacus` | Opacus |
| Privacy / PrivacyMeter | `openresearch-privacymeter` | ML Privacy Meter |
| Robustness / ART | `openresearch-art` | Adversarial Robustness Toolbox |

ColoredMNIST 与 OfficeHome 虽来自同一个 DomainBed 上游仓库，仍应拆成两个研究仓库，因为它们的目标域、数据成本、研究问题和验证协议不同。

任务仓库建议包含：

```text
task-repository/
├── TASK.md                 # Worker 可见目标与规则
├── BASELINE.md             # 上游来源、适配和复现结果
├── train/evaluate entry    # 可运行研究代码
├── public_validation/      # Worker 可调用的公开反馈
├── experiment-manifest/    # 环境、seed、预算、父提交
└── research-log/           # 可公开的假设、patch 和结果索引
```

隐藏 test、私有 metric、reference solution 和最终 verifier 不应出现在 worker 可读的任务仓库中。

### 3.3 独立 evaluator：验收平面

由于 `OpenResearch` 和任务仓库都可能是公开的，隐藏 evaluator 应位于独立的私有仓库、不可读容器镜像或受控远程服务中。

```text
Candidate commit/hash
        ↓
Independent evaluator
        ├── checkout exact candidate
        ├── verify allowed diff
        ├── execute frozen commands
        ├── preserve stdout/stderr/exit code
        ├── evaluate hidden split
        └── emit hash-bound verdict
```

Worker 不得直接写 verdict，也不得拥有 evaluator 凭证。

## 4. Baseline 不是“复制代码就完成”

每个任务的起点需要经过 baseline certification：

1. 记录 upstream URL 与确切 commit；
2. 原样导入或单独记录必要的兼容性适配；
3. 冻结数据、split、环境、硬件、seed、预算和 metric 方向；
4. 在独立 evaluator 中执行 baseline；
5. 保存命令、exit code、日志、artifact hash 和原始 metric；
6. 重复运行以估计方差；
7. 只有通过完整性和可复现检查后才创建不可变 `baseline/v1` tag。

未经 evaluator 跑通的 starter code 只能叫 `imported starter`，不能叫 certified baseline。

## 5. Baseline → Improve 的研究循环

```text
Certified baseline
        ↓
Current global incumbent
        ↓
Worker receives public task + allowed observations + own history
        ↓
Forms hypothesis and one bounded intervention
        ↓
Harness creates candidate branch/commit
        ↓
Public validation execution
        ↓
Keep candidate or retain incumbent
        ↓
Append observation, decision and failure evidence
        ↓
Repeat until budget ends
        ↓
Freeze selected candidate
        ↓
Independent holdout verification
        ↓
Promote or reject
```

每轮必须同时记录：

- `baseline_commit`；
- `parent_incumbent_commit`；
- `candidate_commit`；
- hypothesis 与预期；
- patch hash；
- 执行命令和 exit code；
- public observation；
- keep/discard decision；
- 成本；
- evaluator verdict。

失败和被拒候选保留在 append-only ledger 中，但不进入主线 incumbent。

## 6. “盖章”的含义

Git merge、Agent 自述成功或 validation 分数上涨都不等于盖章。

建议定义三级状态：

### 6.1 Baseline certified

```text
tag: baseline/v1
```

证明起点在冻结环境中可运行、可复现、可被独立 evaluator 测量。

### 6.2 Candidate accepted

```text
tag: accepted/<protocol>/<run-id>
```

证明候选满足：

- allowed diff；
- 命令成功；
- validation 改善规则；
- 所有 correctness/privacy/robustness guardrail；
- artifact 和 commit hash 一致。

### 6.3 Incumbent promoted

```text
tag: incumbent/vN
```

候选冻结后通过 holdout verifier、多 seed/重复确认和预注册晋升条件，才替换任务仓库主线上的 global incumbent。

最终机器可读 verdict 示例：

```json
{
  "schema": "openresearch.acceptance.v1",
  "task": "coloredmnist",
  "protocol": "fml-lite-v1",
  "baseline_commit": "...",
  "candidate_commit": "...",
  "verifier_image": "sha256:...",
  "command_exit_code": 0,
  "constraints_passed": true,
  "holdout_passed": true,
  "decision": "promote",
  "evidence_sha256": "..."
}
```

高风险或外部发布任务可在机器 verdict 之后增加 human acceptance，但人工不能替代前面的执行验证。

## 7. Git 生命周期

建议初始主线：

```text
import/upstream-<sha>      # 原始上游快照
baseline/v1               # 经 evaluator 认证的冻结起点
main                      # 当前已晋升 incumbent
research/<run-id>         # 每次 AutoResearch 候选分支
accepted/<protocol>/<id>  # 通过候选级检查
incumbent/vN              # 通过 holdout 后晋升
```

Worker 不直接 merge、tag 或 push。它只产生候选修改；Git 状态转换由 harness 在 verifier verdict 后执行。

## 8. 与现有系统的关系

- Karpathy AutoResearch：一个小型 LLM 训练仓库就是一个研究问题；`train.py` 是 baseline 和持续修改面，固定 5 分钟训练后 keep/discard。
- FML-bench：中央 benchmark 仓库负责 setup、统一运行与评分，并为每个任务安装独立上游代码和环境；这是跑榜分发结构。
- AutoSOTA：以一个可运行论文代码库和目标作为一次优化单位，循环提出策略、修改、实验并导出最优代码/patch。
- AutoLab：用 monorepo 分发，但每个 `tasks/<task>` 都是自包含环境，拥有自己的 starter、规则、私有 solution 和 verifier。

共同点不是“必须一个 GitHub 仓库”，而是：

> 一个研究单元必须有独立 baseline、目标、环境、可修改边界、实验历史和 verifier。

本项目额外选择一任务一仓库，是为了让这些研究单元成为长期演化、可独立发布和可回放的代码库。

## 9. 实现状态

已实现：

- `task-registry/fml-lite.json` 冻结 8 个任务的上游 commit、metric 和 worker edit surface；
- `openresearch materialize` 从冻结上游和 FML adapter 生成独立研究仓库；
- 8 个公开任务仓库已创建，均以 `imported_starter` 标识并带有不可变 upstream snapshot tag；
- local/SSH remote-fetch/SSH Git-bundle `RunnerBackend` 使用 exact SHA 和 fresh checkout 执行；
- request/completion hash、stdout/stderr、exit code、runtime identity 和 append-only ledger；
- runner contract 的 exact-SHA、失败传播和篡改检测测试。

以下事项尚未盖章：

- baseline 尚未在目标硬件上重复运行；
- 独立 evaluator 的私有承载位置尚未确定；
- acceptance schema 只在文档中定义，尚未由 evaluator 实现；
- 第一个任务的正式 protocol 尚未冻结；
- 尚无任何 FML-Lite candidate 被 accepted 或 promoted。

下一步应选择 PyCIL 完成远端环境安装、端到端 baseline certification 和多 seed 重复，再将冻结 protocol 扩展到其余七项。

## 10. 首次远端传输观察

2026-09-08 在 `hangzhou_server` 上执行 PyCIL contract smoke test：

- 两次 `ssh` remote-fetch 均在访问 GitHub 时失败，分别观察到 TLS 非正常终止和 443 连接超时；
- 两次失败均以 `infrastructure_failed` 回传，没有被解释为候选失败或成功；
- 显式切换到 `ssh-bundle` 后，服务器成功 checkout 请求中的 exact SHA，命令 exit code 为 0，stdout/stderr 和 completion bundle 回传本地；
- 因此该服务器当前应显式使用 `ssh-bundle`，直到它到 GitHub 的网络链路被单独修复并复测。

这只是 runner/transport 验证，不是 PyCIL baseline certification。

## 11. PyCIL baseline certification record (2026-09-08)

第一个 FML-bench-Lite 任务完成 baseline certification，流程与 §4 一致：

- 协议冻结（`fml-lite-pycil-baseline-v1`）先于任何测量：upstream commit
  `f3509b8ca3f2`、FML evaluator sha256 `2e58757a…b6c3b6`、CIFAR-100 md5
  `eb9058c3…2a8d85`、split（30% val / 70% test，split_seed 42）、iCaRL 配置、
  seed 1993、metric `avg_incremental_acc_mean`（NME）、预算 120 分钟、
  晋升标准（3 次成功、exact commit、evaluator hash、环境身份、spread ≤1.0pp、
  独立 verifier PASS）。唯一的测量前修订是把预算从 90 分钟放宽到 120 分钟。
- 环境：`fuxin`（hangzhou_server），Ubuntu 20.04.6，Python 3.8.10 专用 venv，
  torch 2.4.1+cu121，driver 550.78，单卡 RTX 3090（GPU 7 硬件掉卡，协议显式
  固定 `CUDA_VISIBLE_DEVICES=0`）；依赖 lock sha256
  `f8ae96df…0b28a4c`，数据集 sha256 `85cd44d0…5ba677a7`。
- 传输：按 §10 记录的 GitHub 链路不稳定，全部使用 `ssh-bundle`。
- 运行：exact-SHA `15c7804…7b99d9` 三次独立 fresh-checkout 运行全部
  `succeeded`（exit 0），metric 均为 0.59485（spread 0.0pp，cudnn
  deterministic），FML 参考 val 0.59107 / test 0.59517，位于预期范围。
  一次早期失败派发 `pycil-dc34b51bac30`（run.sh bash 花括号展开 bug，
  exit 2）append-only 保留并根因修复，未重写任何记录。
- 验收：`openresearch.certify`（独立进程，不属于 worker/runner/goal 模型）
  机械复算全部 44 项检查后输出 `{"decision": "PASS"}`，evidence_sha256
  `bcea685a…9408d`；verdict 后才更新 `baseline_certified`、创建 tag
  `baseline/v1`（指向被验证 commit）、更新 BASELINE.md 与本仓库文档。
  证据包 auditable 副本提交于 `certification/pycil-baseline-v1/`。
- 隐藏 test split（70%）未评估，保留给独立 holdout verification。

后续七个任务可复用此流程；AutoResearch 候选循环必须以 `baseline/v1` 为起点。
