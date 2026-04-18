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
- `runtime_state` 记录当前阶段、失败次数、验证状态和 blocker 原因。

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
