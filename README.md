# Linear + Claude Code Orchestrator

**自动将 Linear 工单分派给 Claude Code 执行。**
*Auto-dispatches Linear issues to Claude Code.*

---

## 架构 | Architecture

```
OS Scheduler (launchd / systemd / Task Scheduler)
  │ 每 15 秒触发 / every 15s
  ▼
dispatcher.py （一轮即退 / one-shot）
  │ 查 Todo → 移 In Progress → spawn 独立 worker
  ▼
Claude Code worker （独立进程 / detached）
  │ 干活 → 写 output.txt
  ▼
post_result.py （贴评论 + 移 Done）
```

无 daemon、无 PID 文件、无 while 循环。操作系统管调度，Linear 管状态。

*No daemon, no PID file, no while loop. OS handles scheduling, Linear tracks state.*

## 快速安装 | Quick Install

**方式 1：Skill 自动安装**

将 `SKILL.md` 复制到项目 `.claude/skills/`，对 Claude Code 说"安装 linear orchestrator"。自动完成克隆、配置、调度器安装。

*Copy SKILL.md to .claude/skills/ and tell Claude Code "install linear orchestrator".*

**方式 2：手动安装**

```bash
git clone https://github.com/big-lilijiang/linear-claude-orchestrator.git ~/.lco
cat > ~/.lco/env << 'EOF'
LCO_LINEAR_API_KEY=your_key_here
LCO_LINEAR_PROJECT_SLUG=your_project_slug
EOF
source ~/.lco/env && python3 ~/.lco/dispatcher.py
```

## 调度器安装 | Scheduler Setup

| 平台 | 命令 |
|------|------|
| **macOS** | 复制 SKILL.md 中的 plist → `launchctl load` |
| **Linux** | 复制 systemd timer + service → `systemctl --user enable` |
| **Windows** | `schtasks /create /tn "LCO Dispatcher" ...` |

## 项目文件 | Files

```
dispatcher.py          # 一轮分发器 / one-shot dispatcher (~90 lines)
post_result.py         # 结果回报器 / result reporter (~50 lines)
SKILL.md               # Claude Code 安装向导 / setup skill
config.example.yaml    # 环境变量模板 / env template (legacy)
README.md
LICENSE
```

## 要求 | Requirements

- Python 3.10+
- [Claude Code](https://claude.ai/code) CLI
- Linear account with API key
- 零 pip 依赖 / Zero pip dependencies

## License

MIT
