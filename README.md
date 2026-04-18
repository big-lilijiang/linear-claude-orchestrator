# Damon AutoCoding

`Damon AutoCoding` 是一个面向“先规划、后自治执行”的控制面骨架。它的目标不是替代架构设计，而是把你在启动前定义好的规则，转成一个可以持续驱动 Codex worker、验证结果、并最终生成 GitLab Merge Request 的系统。

当前仓库落地的是第一阶段 MVP：

- 架构说明，明确控制面、worker、验证与交付边界。
- `execution_policy`，定义什么情况下系统可以继续推进，什么情况下才允许升级给人。
- `task_contract`，约束每个 worker 任务必须携带的上下文与验收标准。
- `project config`，约束 GitLab 仓库与交付策略。
- `repository_profile`，约束仓库验证命令、worktree 目录和提交策略。
- 一个最小 Python orchestrator，可以校验配置并模拟状态机的下一步决策。
- 一个可直接调用本机 `codex exec` / `codex review` 的 worker adapter。
- 一个可生成 GitLab Merge Request 的交付模块，优先支持 SSH push options，也支持 API payload 生成。
- 一个 `run-task` 主入口，用 `git worktree` 隔离工作区并串起检查、执行和交付计划。

## Repository Layout

- `docs/architecture.md`: 系统架构与阶段目标。
- `configs/execution_policy.yaml`: 默认执行策略。
- `configs/project.yaml`: GitLab 项目配置。
- `configs/repository_profile.yaml`: 仓库命令配置与 worktree 策略。
- `examples/task_contract.yaml`: 示例任务契约。
- `examples/self_test_task.yaml`: 用当前仓库验证主链的自测任务。
- `examples/runtime_state.yaml`: 示例运行态。
- `src/damon_autocoding/`: 控制面骨架代码。

## Quick Start

```bash
PYTHONPATH=src python3 -m damon_autocoding validate \
  --policy configs/execution_policy.yaml \
  --task examples/task_contract.yaml
```

```bash
PYTHONPATH=src python3 -m damon_autocoding simulate \
  --policy configs/execution_policy.yaml \
  --task examples/task_contract.yaml \
  --state examples/runtime_state.yaml
```

```bash
PYTHONPATH=src python3 -m damon_autocoding render-delivery \
  --project configs/project.yaml \
  --source-branch damon/bootstrap-control-plane \
  --title "Bootstrap Damon AutoCoding control plane"
```

```bash
PYTHONPATH=src python3 -m damon_autocoding run-worker \
  --policy configs/execution_policy.yaml \
  --task examples/task_contract.yaml \
  --workdir .
```

```bash
PYTHONPATH=src python3 -m damon_autocoding run-task \
  --policy configs/execution_policy.yaml \
  --project configs/project.yaml \
  --profile configs/repository_profile.yaml \
  --task examples/self_test_task.yaml \
  --repo-root . \
  --dry-run \
  --cleanup \
  --reset-worktree
```

## Design Intent

这套系统默认遵循三条原则：

1. 人类只在规划和真正的 blocker 上介入，不参与每一步执行确认。
2. 外层流程控制必须代码化，不把关键推进节奏托付给一个自由发挥的 manager agent。
3. 多 worker 并发时必须工作区隔离，优先使用 `git worktree` 级别隔离。
