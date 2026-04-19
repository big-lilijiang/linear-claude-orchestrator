# Architecture

## Objective

目标是构建一个“计划先行、执行自治、失败可收敛、结果可交付”的开发控制面。人类在任务开始前负责提供架构、约束和完成定义；系统在执行期间负责拆解任务、调度 worker、验证结果、修复失败并生成 GitLab Merge Request。

## Layering

### 1. Human Planning Layer

这一层不是执行层，而是输入层。系统启动前必须至少具备以下四类输入：

- `architecture`: 目标架构、模块边界、关键设计决策。
- `constraints`: 技术限制、风险边界、禁止触碰区域。
- `definition_of_done`: 验收要求、测试范围、文档要求、交付标准。
- `execution_policy`: 系统在执行时的自治边界与升级规则。

### 2. Control Plane

控制面由代码驱动，而不是由一个自由发挥的 agent 驱动。它负责：

- 任务拆分与依赖管理。
- worker 分配与并发调度。
- 重试、超时、失败收敛。
- Git 分支策略、工作区隔离。
- CI 轮询与失败修复触发。
- GitLab Merge Request 创建与状态追踪。

### 3. Worker Layer

Worker 是执行单元。第一阶段默认只要求以下角色概念存在，不要求全部接通：

- `planner`: 产出任务 DAG 与任务粒度。
- `repo_analyst`: 识别仓库结构、命令入口与风险目录。
- `implementer`: 编码、测试、修复。
- `reviewer`: 进行 diff 审阅、检查回归风险与测试缺口。
- `ci_fixer`: 针对流水线失败继续收敛。
- `mr_writer`: 生成 MR 标题、描述、验证摘要与风险说明。

### 4. Delivery Layer

交付层只做确定性动作：

- 推送分支。
- 创建 GitLab Merge Request。
- 附带测试证据、风险摘要和后续观察点。

## Integration Strategy

### Codex Execution

V1 直接复用本机 `codex exec` 和 `codex review`，不额外发明 worker 协议。控制面只负责：

- 按任务契约渲染 prompt。
- 指定工作目录、sandbox、输出路径。
- 记录退出码、标准输出、标准错误和最终消息。

### GitLab Delivery

V1 的交付优先顺序如下：

1. 通过 Git SSH remote + push options 直接创建 Merge Request。
2. 如果存在 API token，则允许改走 GitLab REST API 创建 Merge Request。

这意味着即便没有预置 `glab` 或独立 API client，系统依然可以形成真实交付闭环。

## Runtime Contract

系统推进节奏不依赖“agent 想不想问人”，而依赖以下契约：

- `execution_policy` 规定何时自动推进，何时升级。
- `task_contract` 规定 worker 收到任务时必须有哪些上下文。
- `repository_profile` 规定这个仓库该怎么跑 setup / lint / test / static analysis，以及 worktree 放在哪里。
- `runtime_state` 记录当前阶段、失败次数、验证状态和 blocker 原因。

其中 `task_contract.repository.base_ref` 是一个重要补充字段：它允许任务明确声明“从哪个本地或远端 ref 起分支”，从而避免仓库还没合回 `main` 时，自测或串行任务错误地从远端默认分支启动。

## Non-Goals For V1

第一阶段不追求：

- 完整 UI。
- 任意仓库零配置接入。
- 跨仓库事务型并发交付。
- 无边界的 agent 自主规划。

## V1 Implementation Scope

第一阶段只解决四个问题：

1. 把执行策略和任务契约落成配置与模型。
2. 提供一个最小状态机，验证系统是否能在“不问人”的前提下继续推进。
3. 为后续接入 Agents SDK / Codex worker 预留明确接口。
4. 明确 GitLab MR 作为默认交付物，而不是聊天文本作为交付物。

## V1.5 Execution Path

当前代码库已经具备一条最小主链：

1. 根据 `task_contract` 和 `repository_profile` 创建隔离 worktree。
2. 在 worktree 中执行仓库级检查命令。
3. 在非 dry-run 模式下调用本机 `codex exec`。
4. 渲染 GitLab Merge Request 交付命令；可选地直接 push 创建 MR。

这条主链仍然是单 worker、单任务模式，但它已经把“从配置到执行再到交付”的路径固化下来了。

## V2 CLI Product Layer

为了满足“先交互式规划，再进入自治执行，最后以 PR 结束”的体验，当前代码库新增了四个命令层概念：

1. `damon start`
2. `damon execute`
3. `damon complete-pr`
4. `damon blocked-pr`

这四个命令把一次 run 的生命周期固定为：

- 在 `start` 阶段扫描仓库、与用户澄清目标、生成 dossier。
- 在 `execute` 阶段读取冻结 dossier，调用 worktree、worker、验证和重试逻辑。
- 在成功场景下进入 `complete-pr`。
- 在失败但已有可交付产物时进入 `blocked-pr`。

对应的持久化目录位于仓库下：

- `.damon/runs/<RUN_ID>/run.yaml`
- `.damon/runs/<RUN_ID>/dossier/*`
- `.damon/runs/<RUN_ID>/reports/*`
