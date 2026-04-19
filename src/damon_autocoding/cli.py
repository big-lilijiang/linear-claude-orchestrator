from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .sidecar import SidecarSupervisor


def bi(zh: str, en: str) -> str:
    return f"{zh} / {en}"


class DamonArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        if self.prog == "damon":
            return "\n".join(
                [
                    "usage: damon [-h] {attach,loop,status,pr} ...",
                    "",
                    bi(
                        "Damon 是一个围绕 Codex 的旁路推进器：保留 Codex 原生体验，只负责自动继续、记录过程和收尾成 PR。",
                        "Damon is a Codex sidecar supervisor: keep the native Codex experience, add auto-continue, tracking, and PR handoff.",
                    ),
                    "",
                    "commands:",
                    f"  attach  {bi('绑定当前仓库到一个 Codex session', 'bind the current repository to a Codex session')}",
                    f"  loop    {bi('自动继续推进 N 个 checkpoint', 'auto-advance N checkpoints')}",
                    f"  status  {bi('查看当前绑定和最近推进结果', 'show the current binding and recent progress')}",
                    f"  pr      {bi('把当前结果推成 PR', 'turn the current result into a merge request')}",
                    "",
                    bi("快速开始:", "Quick start:"),
                    "  damon attach",
                    "  damon loop 10",
                    "  damon status",
                    "  damon pr",
                    "",
                    "options:",
                    "  -h, --help   show this help message and exit",
                    "",
                ]
            )
        return super().format_help()


def build_parser() -> argparse.ArgumentParser:
    parser = DamonArgumentParser(
        prog="damon",
        formatter_class=argparse.RawTextHelpFormatter,
        description=bi(
            "保留 Codex 原生体验，只补自动推进和状态跟踪。",
            "Keep the native Codex experience and only add auto-progress and tracking.",
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    attach_parser = subparsers.add_parser(
        "attach",
        help=bi("绑定到当前仓库最近的 Codex session", "bind to the latest Codex session for this repo"),
    )
    attach_parser.add_argument("--repo", default=".", help=bi("目标仓库根目录", "target repository root"))
    attach_parser.add_argument("--session", help=bi("显式指定 Codex session id", "explicit Codex session id"))

    loop_parser = subparsers.add_parser(
        "loop",
        help=bi("自动继续推进 N 个 checkpoint", "auto-advance N checkpoints"),
    )
    loop_parser.add_argument("steps", nargs="?", type=int, default=1, help=bi("要推进的步数", "number of steps to advance"))
    loop_parser.add_argument("--repo", default=".", help=bi("目标仓库根目录", "target repository root"))
    loop_parser.add_argument("--instruction", help=bi("附加给 Codex 的本轮指令", "extra instruction for this loop run"))

    status_parser = subparsers.add_parser(
        "status",
        help=bi("查看当前绑定和最近推进结果", "show the current binding and recent progress"),
    )
    status_parser.add_argument("--repo", default=".", help=bi("目标仓库根目录", "target repository root"))
    status_parser.add_argument("--json", action="store_true", help=bi("输出 JSON", "print JSON"))

    pr_parser = subparsers.add_parser(
        "pr",
        help=bi("把当前结果推成 PR", "turn the current result into a merge request"),
    )
    pr_parser.add_argument("--repo", default=".", help=bi("目标仓库根目录", "target repository root"))
    pr_parser.add_argument(
        "--kind",
        choices=["auto", "complete", "blocked"],
        default="auto",
        help=bi("PR 类型：自动、完整、阻塞", "PR kind: auto, complete, or blocked"),
    )

    return parser


def main() -> int:
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    args = parser.parse_args()

    if args.command == "attach":
        supervisor = SidecarSupervisor(repo_root=args.repo)
        state = supervisor.attach(session_id=args.session)
        print(
            json.dumps(
                {
                    "repo_root": state.repo_root,
                    "session_id": state.session.session_id,
                    "session_file": state.session.session_file,
                    "working_branch": state.working_branch,
                    "target_branch": state.target_branch,
                    "next_command": f"damon loop 10 --repo {Path(state.repo_root)}",
                },
                indent=2,
            )
        )
        return 0

    if args.command == "loop":
        supervisor = SidecarSupervisor(repo_root=args.repo)
        print(bi("Codex 正在继续推进...", "Codex is continuing the work..."))
        state = supervisor.loop(steps=args.steps, extra_instruction=args.instruction)
        latest = state.history[-1] if state.history else None
        payload = {
            "working_branch": state.working_branch,
            "target_branch": state.target_branch,
            "latest_status": state.latest_status,
            "latest_summary": state.latest_summary,
            "history_count": len(state.history),
        }
        if latest is not None:
            payload["latest_step"] = latest.model_dump(mode="json")
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "status":
        supervisor = SidecarSupervisor(repo_root=args.repo)
        state, snapshot = supervisor.status()
        payload = {
            "repo_root": state.repo_root,
            "session_id": state.session.session_id,
            "working_branch": state.working_branch,
            "target_branch": state.target_branch,
            "remote_url": state.remote_url,
            "latest_status": state.latest_status,
            "latest_summary": state.latest_summary,
            "current_branch": snapshot.current_branch,
            "dirty": snapshot.dirty,
            "history_count": len(state.history),
            "recent_steps": [step.model_dump(mode="json") for step in state.history[-5:]],
            "latest_pr_report": state.latest_pr_report,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
            return 0
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "pr":
        supervisor = SidecarSupervisor(repo_root=args.repo)
        print(bi("Codex 正在整理 PR...", "Codex is preparing the PR..."))
        payload = supervisor.open_pr(kind=args.kind)
        print(json.dumps(payload, indent=2))
        return 0

    parser.print_help()
    return 0
