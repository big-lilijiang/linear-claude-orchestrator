from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_policy, load_project, load_repository_profile, load_runtime_state, load_task
from .gitlab import GitLabDelivery, MergeRequestSpec
from .orchestrator import ControlPlane
from .runs import PlannerIO, RunManager, StartFlow, create_blocked_pr, create_complete_pr, execute_run
from .task_runner import TaskRunner, dump_task_run_report
from .workers import CodexCLIWorker, dump_worker_result


def bi(zh: str, en: str) -> str:
    return f"{zh} / {en}"


PRIMARY_COMMANDS = {
    "start",
    "execute",
    "complete-pr",
    "blocked-pr",
}


class DamonHelpFormatter(argparse.RawTextHelpFormatter):
    pass


class DamonArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        if self.prog == "damon":
            return "\n".join(
                [
                    "usage: damon [-h] {start,execute,complete-pr,blocked-pr} ...",
                    "",
                    bi(
                        "Damon 是一个先规划、后执行、以 PR 为结束态的开发命令行。",
                        "Damon is a planning-first autonomous coding CLI that ends in a PR.",
                    ),
                    "",
                    "commands:",
                    f"  start        {bi('交互式规划并生成 run dossier', 'interactive planning and dossier generation')}",
                    f"  execute      {bi('执行已冻结的 dossier', 'execute a frozen dossier')}",
                    f"  complete-pr  {bi('把成功 run 推成完整 PR', 'push a successful run as a complete PR')}",
                    f"  blocked-pr   {bi('把失败 run 推成阻塞 PR', 'push a failed run as a blocked PR')}",
                    "",
                    bi("快速开始:", "Quick start:"),
                    '  damon start --repo . --goal "实现一个功能并提 PR"',
                    "  damon execute --repo . --latest",
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
        description=bi(
            "Damon 是一个先规划、后执行、以 PR 为结束态的开发命令行。",
            "Damon is a planning-first autonomous coding CLI that ends in a PR.",
        ),
        epilog="\n".join(
            [
                bi("快速开始:", "Quick start:"),
                "  damon start --repo . --goal \"实现一个功能并提 PR\"",
                "  damon execute --repo . --latest",
                "",
                bi("常用命令:", "Common commands:"),
                f"  start       {bi('交互式规划并生成 dossier', 'interactive planning and dossier generation')}",
                f"  execute     {bi('按 dossier 执行任务', 'execute a frozen dossier')}",
                f"  complete-pr {bi('成功后推送完整 PR', 'push a complete PR after success')}",
                f"  blocked-pr  {bi('失败时推送阻塞 PR', 'push a blocked PR when blocked')}",
            ]
        ),
        formatter_class=DamonHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    start_parser = subparsers.add_parser(
        "start",
        help=bi("交互式规划并生成 run dossier", "interactive planning and dossier generation"),
        description=bi(
            "扫描仓库、与你澄清目标和约束，并生成一份可冻结的 run dossier。",
            "Scan the repository, clarify scope and constraints with you, and generate a run dossier.",
        ),
        formatter_class=DamonHelpFormatter,
    )
    start_parser.add_argument("--repo", default=".", help=bi("目标仓库根目录", "target repository root"))
    start_parser.add_argument("--goal", help=bi("初始目标；省略时进入提问", "initial goal; if omitted, the planner will ask"))

    execute_parser = subparsers.add_parser(
        "execute",
        help=bi("执行已冻结的 dossier", "execute a frozen dossier"),
        description=bi(
            "读取 run dossier，创建 worktree，调用 Codex 和仓库验证命令推进任务。",
            "Read a run dossier, create a worktree, invoke Codex, and run repository checks.",
        ),
        formatter_class=DamonHelpFormatter,
    )
    execute_parser.add_argument("--repo", default=".", help=bi("目标仓库根目录", "target repository root"))
    execute_parser.add_argument("--run", help=bi("指定 run ID", "explicit run ID"))
    execute_parser.add_argument("--latest", action="store_true", help=bi("执行最新 run", "execute the latest run"))
    execute_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=bi("只跑流程和验证，不调用 Codex、不推 PR", "run orchestration and checks only; do not invoke Codex or push PRs"),
    )
    execute_parser.add_argument("--cleanup", action="store_true", help=bi("结束后删除 worktree", "remove the worktree when finished"))
    execute_parser.add_argument(
        "--reset-worktree",
        action="store_true",
        help=bi("如果 worktree 已存在则重建", "replace the worktree if it already exists"),
    )
    execute_parser.add_argument("--worker-timeout-seconds", type=int, help=bi("覆盖 Codex 执行超时", "override codex exec timeout"))
    execute_parser.add_argument("--review-timeout-seconds", type=int, help=bi("覆盖 Codex review 超时", "override codex review timeout"))

    complete_parser = subparsers.add_parser(
        "complete-pr",
        help=bi("把成功 run 推成完整 PR", "push a successful run as a complete PR"),
        description=bi("把最近一次成功执行的结果推成完整 PR。", "Push the latest successful execution as a complete PR."),
        formatter_class=DamonHelpFormatter,
    )
    complete_parser.add_argument("--repo", default=".", help=bi("目标仓库根目录", "target repository root"))
    complete_parser.add_argument("--run", help=bi("指定 run ID", "explicit run ID"))
    complete_parser.add_argument("--latest", action="store_true", help=bi("使用最新 run", "use the latest run"))

    blocked_parser = subparsers.add_parser(
        "blocked-pr",
        help=bi("把失败 run 推成阻塞 PR", "push a failed run as a blocked PR"),
        description=bi("把最近一次失败执行的结果推成 blocked PR。", "Push the latest failed execution as a blocked PR."),
        formatter_class=DamonHelpFormatter,
    )
    blocked_parser.add_argument("--repo", default=".", help=bi("目标仓库根目录", "target repository root"))
    blocked_parser.add_argument("--run", help=bi("指定 run ID", "explicit run ID"))
    blocked_parser.add_argument("--latest", action="store_true", help=bi("使用最新 run", "use the latest run"))

    validate_parser = subparsers.add_parser(
        "validate",
        help="",
        formatter_class=DamonHelpFormatter,
    )
    validate_parser.add_argument("--policy", required=True, help=bi("执行策略 YAML 路径", "execution policy YAML path"))
    validate_parser.add_argument("--task", required=True, help=bi("任务契约 YAML 路径", "task contract YAML path"))
    validate_parser.add_argument("--project", help=bi("项目配置 YAML 路径", "project YAML path"))
    validate_parser.add_argument("--profile", help=bi("仓库配置 YAML 路径", "repository profile YAML path"))

    simulate_parser = subparsers.add_parser(
        "simulate",
        help="",
        formatter_class=DamonHelpFormatter,
    )
    simulate_parser.add_argument("--policy", required=True, help=bi("执行策略 YAML 路径", "execution policy YAML path"))
    simulate_parser.add_argument("--task", required=True, help=bi("任务契约 YAML 路径", "task contract YAML path"))
    simulate_parser.add_argument("--state", required=True, help=bi("运行态 YAML 路径", "runtime state YAML path"))

    render_delivery = subparsers.add_parser(
        "render-delivery",
        help="",
        formatter_class=DamonHelpFormatter,
    )
    render_delivery.add_argument("--project", required=True, help=bi("项目配置 YAML 路径", "project YAML path"))
    render_delivery.add_argument("--source-branch", required=True, help=bi("源分支", "source branch"))
    render_delivery.add_argument("--target-branch", help=bi("目标分支，默认取 project 配置", "target branch; defaults to project config"))
    render_delivery.add_argument("--title", required=True, help=bi("Merge Request 标题", "merge request title"))
    render_delivery.add_argument(
        "--description",
        default="Automated delivery by Damon AutoCoding.",
        help=bi("Merge Request 描述", "merge request description"),
    )

    run_worker = subparsers.add_parser(
        "run-worker",
        help="",
        formatter_class=DamonHelpFormatter,
    )
    run_worker.add_argument("--policy", required=True, help=bi("执行策略 YAML 路径", "execution policy YAML path"))
    run_worker.add_argument("--task", required=True, help=bi("任务契约 YAML 路径", "task contract YAML path"))
    run_worker.add_argument("--workdir", required=True, help=bi("仓库工作目录", "repository working directory"))
    run_worker.add_argument("--output", help=bi("最终消息输出文件", "output file for the final message"))
    run_worker.add_argument("--timeout-seconds", type=int, help=bi("Codex 执行超时", "codex exec timeout"))

    review_worker = subparsers.add_parser(
        "review-worker",
        help="",
        formatter_class=DamonHelpFormatter,
    )
    review_worker.add_argument("--policy", required=True, help=bi("执行策略 YAML 路径", "execution policy YAML path"))
    review_worker.add_argument("--workdir", required=True, help=bi("仓库工作目录", "repository working directory"))
    review_worker.add_argument("--base", required=True, help=bi("review 的基线分支", "base branch for review"))
    review_worker.add_argument("--timeout-seconds", type=int, help=bi("Codex review 超时", "codex review timeout"))

    run_task = subparsers.add_parser(
        "run-task",
        help="",
        formatter_class=DamonHelpFormatter,
    )
    run_task.add_argument("--policy", required=True, help=bi("执行策略 YAML 路径", "execution policy YAML path"))
    run_task.add_argument("--project", required=True, help=bi("项目配置 YAML 路径", "project YAML path"))
    run_task.add_argument("--profile", required=True, help=bi("仓库配置 YAML 路径", "repository profile YAML path"))
    run_task.add_argument("--task", required=True, help=bi("任务契约 YAML 路径", "task contract YAML path"))
    run_task.add_argument("--repo-root", default=".", help=bi("目标仓库根目录", "target repository root"))
    run_task.add_argument(
        "--dry-run",
        action="store_true",
        help=bi("只跑 worktree 与验证，不调用 Codex、不推送", "prepare worktree and checks only; do not invoke Codex or push"),
    )
    run_task.add_argument("--push", action="store_true", help=bi("成功时推送分支并创建 MR", "push the branch and create an MR on success"))
    run_task.add_argument("--cleanup", action="store_true", help=bi("结束后删除 worktree", "remove the worktree when finished"))
    run_task.add_argument("--reset-worktree", action="store_true", help=bi("如果 worktree 已存在则重建", "replace the worktree if it already exists"))
    run_task.add_argument("--worker-timeout-seconds", type=int, help=bi("Codex 执行超时", "codex exec timeout"))
    run_task.add_argument("--review-timeout-seconds", type=int, help=bi("Codex review 超时", "codex review timeout"))

    return parser


def resolve_run_id(*, repo: str | Path, run: str | None, latest: bool) -> str:
    if run:
        return run
    if latest:
        return RunManager(repo).latest_run_id()
    raise ValueError("Specify --run or --latest")


def main() -> int:
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    args = parser.parse_args()

    if args.command == "start":
        manifest, paths = StartFlow().run(
            repo_root=args.repo,
            goal=args.goal,
            io=PlannerIO(stdin=sys.stdin, stdout=sys.stdout),
        )
        print(
            json.dumps(
                {
                    "run_id": manifest.run_id,
                    "status": manifest.status,
                    "repo_root": manifest.repo_root,
                    "dossier_root": str(paths.root),
                    "next_command": f"damon execute --repo {manifest.repo_root} --run {manifest.run_id}",
                },
                indent=2,
            )
        )
        return 0

    if args.command == "execute":
        run_id = resolve_run_id(repo=args.repo, run=args.run, latest=args.latest)
        manifest, payload = execute_run(
            repo_root=args.repo,
            run_id=run_id,
            dry_run=args.dry_run,
            cleanup=args.cleanup,
            reset_worktree=args.reset_worktree,
            worker_timeout_seconds=args.worker_timeout_seconds,
            review_timeout_seconds=args.review_timeout_seconds,
        )
        print(
            json.dumps(
                {
                    "run_id": manifest.run_id,
                    "status": manifest.status,
                    "report": payload,
                    "latest_execute_report": manifest.latest_execute_report,
                    "latest_delivery_report": manifest.latest_delivery_report,
                },
                indent=2,
            )
        )
        return 0

    if args.command == "complete-pr":
        run_id = resolve_run_id(repo=args.repo, run=args.run, latest=args.latest)
        print(json.dumps(create_complete_pr(repo_root=args.repo, run_id=run_id), indent=2))
        return 0

    if args.command == "blocked-pr":
        run_id = resolve_run_id(repo=args.repo, run=args.run, latest=args.latest)
        print(json.dumps(create_blocked_pr(repo_root=args.repo, run_id=run_id), indent=2))
        return 0

    if args.command == "validate":
        load_policy(args.policy)
        load_task(args.task)
        if args.project:
            load_project(args.project)
        if args.profile:
            load_repository_profile(args.profile)
        print("Validation OK")
        return 0

    if args.command == "simulate":
        policy = load_policy(args.policy)
        task = load_task(args.task)
        state = load_runtime_state(args.state)
        decision = ControlPlane(policy).decide(task, state)
        print(json.dumps(decision.model_dump(mode="json"), indent=2))
        return 0

    if args.command == "render-delivery":
        project = load_project(args.project)
        spec = MergeRequestSpec(
            source_branch=args.source_branch,
            target_branch=args.target_branch or project.default_target_branch,
            title=args.title,
            description=args.description,
            draft=project.delivery.draft_merge_requests,
            labels=project.delivery.default_labels,
        )
        delivery = GitLabDelivery(project)
        payload = {
            "push_command": delivery.build_push_command(spec),
            "api_endpoint": project.gitlab.merge_requests_api,
            "api_payload": delivery.build_api_payload(spec),
            "project_web_url": project.gitlab.project_web_url,
        }
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "run-worker":
        policy = load_policy(args.policy)
        task = load_task(args.task)
        result = CodexCLIWorker(policy).run(
            task,
            workdir=args.workdir,
            output_path=args.output,
            timeout_seconds=args.timeout_seconds,
        )
        print(dump_worker_result(result))
        return 0

    if args.command == "review-worker":
        policy = load_policy(args.policy)
        result = CodexCLIWorker(policy).review(
            workdir=args.workdir,
            base_branch=args.base,
            timeout_seconds=args.timeout_seconds,
        )
        print(dump_worker_result(result))
        return 0

    if args.command == "run-task":
        policy = load_policy(args.policy)
        project = load_project(args.project)
        profile = load_repository_profile(args.profile)
        task = load_task(args.task)
        report = TaskRunner(project=project, policy=policy, profile=profile).run(
            task,
            repo_root=args.repo_root,
            dry_run=args.dry_run,
            push=args.push,
            cleanup=args.cleanup,
            reset_worktree=args.reset_worktree,
            worker_timeout_seconds=args.worker_timeout_seconds,
            review_timeout_seconds=args.review_timeout_seconds,
        )
        print(dump_task_run_report(report))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2
