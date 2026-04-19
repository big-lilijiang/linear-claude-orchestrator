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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Damon CLI for planning, executing, and delivering autonomous coding runs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Create a run dossier through an interactive planning session.")
    start_parser.add_argument("--repo", default=".", help="Target repository root.")
    start_parser.add_argument("--goal", help="Initial goal statement. If omitted, the planner will ask for it.")

    execute_parser = subparsers.add_parser("execute", help="Execute a frozen run dossier.")
    execute_parser.add_argument("--repo", default=".", help="Target repository root.")
    execute_parser.add_argument("--run", help="Explicit run ID.")
    execute_parser.add_argument("--latest", action="store_true", help="Execute the latest run under .damon/runs.")
    execute_parser.add_argument("--dry-run", action="store_true", help="Run verification flow without invoking Codex or pushing PRs.")
    execute_parser.add_argument("--cleanup", action="store_true", help="Remove the worktree after execution finishes.")
    execute_parser.add_argument("--reset-worktree", action="store_true", help="Replace an existing worktree for the run.")
    execute_parser.add_argument("--worker-timeout-seconds", type=int, help="Optional timeout override for codex exec.")
    execute_parser.add_argument("--review-timeout-seconds", type=int, help="Optional timeout override for codex review.")

    complete_parser = subparsers.add_parser("complete-pr", help="Push the latest successful run as a merge request.")
    complete_parser.add_argument("--repo", default=".", help="Target repository root.")
    complete_parser.add_argument("--run", help="Explicit run ID.")
    complete_parser.add_argument("--latest", action="store_true", help="Use the latest run.")

    blocked_parser = subparsers.add_parser("blocked-pr", help="Push the latest failed run as a blocked merge request.")
    blocked_parser.add_argument("--repo", default=".", help="Target repository root.")
    blocked_parser.add_argument("--run", help="Explicit run ID.")
    blocked_parser.add_argument("--latest", action="store_true", help="Use the latest run.")

    validate_parser = subparsers.add_parser("validate", help="Validate policy and task files.")
    validate_parser.add_argument("--policy", required=True, help="Path to execution policy YAML.")
    validate_parser.add_argument("--task", required=True, help="Path to task contract YAML.")
    validate_parser.add_argument("--project", help="Optional path to project YAML.")
    validate_parser.add_argument("--profile", help="Optional path to repository profile YAML.")

    simulate_parser = subparsers.add_parser("simulate", help="Simulate the next control-plane decision.")
    simulate_parser.add_argument("--policy", required=True, help="Path to execution policy YAML.")
    simulate_parser.add_argument("--task", required=True, help="Path to task contract YAML.")
    simulate_parser.add_argument("--state", required=True, help="Path to runtime state YAML.")

    render_delivery = subparsers.add_parser("render-delivery", help="Render GitLab delivery command and API payload.")
    render_delivery.add_argument("--project", required=True, help="Path to project YAML.")
    render_delivery.add_argument("--source-branch", required=True, help="Source branch for the merge request.")
    render_delivery.add_argument("--target-branch", help="Target branch. Defaults to project config.")
    render_delivery.add_argument("--title", required=True, help="Merge request title.")
    render_delivery.add_argument("--description", default="Automated delivery by Damon AutoCoding.", help="Merge request description.")

    run_worker = subparsers.add_parser("run-worker", help="Run a codex worker for a task contract.")
    run_worker.add_argument("--policy", required=True, help="Path to execution policy YAML.")
    run_worker.add_argument("--task", required=True, help="Path to task contract YAML.")
    run_worker.add_argument("--workdir", required=True, help="Repository working directory.")
    run_worker.add_argument("--output", help="Optional path for the final message output file.")
    run_worker.add_argument("--timeout-seconds", type=int, help="Optional timeout override for codex exec.")

    review_worker = subparsers.add_parser("review-worker", help="Run codex review on the current repository.")
    review_worker.add_argument("--policy", required=True, help="Path to execution policy YAML.")
    review_worker.add_argument("--workdir", required=True, help="Repository working directory.")
    review_worker.add_argument("--base", required=True, help="Base branch for review.")
    review_worker.add_argument("--timeout-seconds", type=int, help="Optional timeout override for codex review.")

    run_task = subparsers.add_parser("run-task", help="Execute the main task orchestration flow.")
    run_task.add_argument("--policy", required=True, help="Path to execution policy YAML.")
    run_task.add_argument("--project", required=True, help="Path to project YAML.")
    run_task.add_argument("--profile", required=True, help="Path to repository profile YAML.")
    run_task.add_argument("--task", required=True, help="Path to task contract YAML.")
    run_task.add_argument("--repo-root", default=".", help="Repository root to operate on.")
    run_task.add_argument("--dry-run", action="store_true", help="Prepare worktree and run checks without invoking Codex or push.")
    run_task.add_argument("--push", action="store_true", help="Push the task branch and create a merge request when the run succeeds.")
    run_task.add_argument("--cleanup", action="store_true", help="Remove the worktree after the run finishes.")
    run_task.add_argument("--reset-worktree", action="store_true", help="Replace an existing worktree at the task path.")
    run_task.add_argument("--worker-timeout-seconds", type=int, help="Optional timeout override for codex exec.")
    run_task.add_argument("--review-timeout-seconds", type=int, help="Optional timeout override for codex review.")

    return parser


def resolve_run_id(*, repo: str | Path, run: str | None, latest: bool) -> str:
    if run:
        return run
    if latest:
        return RunManager(repo).latest_run_id()
    raise ValueError("Specify --run or --latest")


def main() -> int:
    parser = build_parser()
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
