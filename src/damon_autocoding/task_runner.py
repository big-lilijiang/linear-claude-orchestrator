from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from .gitlab import GitLabDelivery, MergeRequestSpec
from .models import ExecutionPolicy, WorkerTask
from .project import ProjectConfig
from .repo_profile import CommandSpec, RepositoryProfile
from .workers import CodexCLIWorker, WorkerRunResult
from .workspace import CommandExecutionResult, GitWorktreeManager, WorktreeContext, run_shell_command


@dataclass(slots=True)
class DeliveryReport:
    push_command: list[str]
    api_endpoint: str
    api_payload: dict[str, Any]
    pushed: bool
    push_exit_code: int | None = None
    push_stdout: str | None = None
    push_stderr: str | None = None
    merge_request_url_hint: str | None = None


@dataclass(slots=True)
class TaskRunReport:
    task_id: str
    dry_run: bool
    success: bool
    worktree_path: str
    working_branch: str
    base_ref: str
    cleanup_performed: bool
    setup_results: list[CommandExecutionResult]
    lint_results: list[CommandExecutionResult]
    test_results: list[CommandExecutionResult]
    static_analysis_results: list[CommandExecutionResult]
    worker_result: dict[str, Any] | None
    review_result: dict[str, Any] | None
    commit_sha: str | None
    delivery: DeliveryReport


class TaskRunner:
    def __init__(
        self,
        *,
        project: ProjectConfig,
        policy: ExecutionPolicy,
        profile: RepositoryProfile,
        worker: CodexCLIWorker | None = None,
    ) -> None:
        self.project = project
        self.policy = policy
        self.profile = profile
        self.worker = worker or CodexCLIWorker(policy)
        self.delivery = GitLabDelivery(project)

    def run(
        self,
        task: WorkerTask,
        *,
        repo_root: str | Path,
        dry_run: bool = False,
        push: bool = False,
        cleanup: bool = False,
        reset_worktree: bool = False,
        worker_timeout_seconds: int | None = None,
        review_timeout_seconds: int | None = None,
    ) -> TaskRunReport:
        repo_root = Path(repo_root).resolve()
        manager = GitWorktreeManager(repo_root, remote_name=self.project.remote_name)
        branch_existed = manager.local_branch_exists(task.repository.working_branch)
        worktree_path = repo_root / self.profile.worktree_root / self._slug(f"{task.task_id}-{task.repository.working_branch}")
        worktree = manager.prepare(
            target_branch=task.repository.target_branch,
            working_branch=task.repository.working_branch,
            worktree_path=worktree_path,
            base_ref=task.repository.base_ref,
            reset=reset_worktree,
        )

        cleanup_performed = False
        report: TaskRunReport | None = None
        try:
            task_for_run = task.model_copy(deep=True)
            task_for_run.repository.path = worktree.path

            setup_results = self._run_command_group(self.profile.setup_commands, cwd=worktree.path)
            worker_result = self._run_worker(task_for_run, worktree=worktree, dry_run=dry_run, timeout_seconds=worker_timeout_seconds)
            lint_results = self._run_lint_commands(cwd=worktree.path)
            test_results = self._run_test_commands(task_for_run, cwd=worktree.path)
            static_analysis_results = self._run_static_analysis_commands(cwd=worktree.path)
            review_result = self._run_review(worktree=worktree, dry_run=dry_run, timeout_seconds=review_timeout_seconds)

            required_checks_ok = self._all_required_checks_passed(
                setup_results,
                lint_results,
                test_results,
                static_analysis_results,
                worker_result,
                review_result,
            )

            commit_sha = None
            if not dry_run and self.profile.commit_changes and required_checks_ok and manager.has_uncommitted_changes(worktree.path):
                commit_sha = manager.commit_all(
                    worktree.path,
                    message=self.profile.commit_message_template.format(task_id=task.task_id, title=task.title),
                )

            delivery_report = self._build_delivery_report(
                task,
                worktree=worktree,
                push=push,
                dry_run=dry_run,
                commit_sha=commit_sha,
            )

            success = required_checks_ok and (not push or delivery_report.pushed and delivery_report.push_exit_code == 0)
            report = TaskRunReport(
                task_id=task.task_id,
                dry_run=dry_run,
                success=success,
                worktree_path=str(worktree.path),
                working_branch=worktree.working_branch,
                base_ref=worktree.base_ref,
                cleanup_performed=cleanup_performed,
                setup_results=setup_results,
                lint_results=lint_results,
                test_results=test_results,
                static_analysis_results=static_analysis_results,
                worker_result=self._serialize_worker_result(worker_result),
                review_result=self._serialize_worker_result(review_result),
                commit_sha=commit_sha,
                delivery=delivery_report,
            )
            return report
        finally:
            if cleanup:
                manager.remove(worktree.path, force=True, missing_ok=True)
                if dry_run and not branch_existed:
                    manager.delete_branch(task.repository.working_branch, force=True, missing_ok=True)
                cleanup_performed = True
            if report is not None:
                report.cleanup_performed = cleanup_performed

    def _run_command_group(self, commands: list[CommandSpec], *, cwd: Path) -> list[CommandExecutionResult]:
        return [
            run_shell_command(
                name=command.name,
                command=command.command,
                cwd=cwd,
                required=command.required,
                timeout_seconds=command.timeout_seconds,
            )
            for command in commands
        ]

    def _run_worker(
        self,
        task: WorkerTask,
        *,
        worktree: WorktreeContext,
        dry_run: bool,
        timeout_seconds: int | None,
    ) -> WorkerRunResult | None:
        if dry_run or not task.deliverables.code_changes_required:
            return None
        return self.worker.run(task, workdir=str(worktree.path), timeout_seconds=timeout_seconds)

    def _run_review(
        self,
        *,
        worktree: WorktreeContext,
        dry_run: bool,
        timeout_seconds: int | None,
    ) -> WorkerRunResult | None:
        if dry_run or not self.profile.run_review:
            return None
        return self.worker.review(workdir=str(worktree.path), base_branch=worktree.base_ref, timeout_seconds=timeout_seconds)

    def _run_lint_commands(self, *, cwd: Path) -> list[CommandExecutionResult]:
        if not self.policy.verification.require_lint:
            return []
        return self._run_command_group(self.profile.lint_commands, cwd=cwd)

    def _run_test_commands(self, task: WorkerTask, *, cwd: Path) -> list[CommandExecutionResult]:
        if not (self.policy.verification.require_unit_tests or task.deliverables.tests_required):
            return []
        return self._run_command_group(self.profile.test_commands, cwd=cwd)

    def _run_static_analysis_commands(self, *, cwd: Path) -> list[CommandExecutionResult]:
        if not self.policy.verification.require_static_analysis:
            return []
        return self._run_command_group(self.profile.static_analysis_commands, cwd=cwd)

    def _all_required_checks_passed(
        self,
        *groups: list[CommandExecutionResult] | WorkerRunResult | None,
    ) -> bool:
        for group in groups:
            if group is None:
                continue
            if isinstance(group, list):
                for result in group:
                    if result.required and result.exit_code != 0:
                        return False
                continue
            if group.exit_code != 0:
                return False
        return True

    def _build_delivery_report(
        self,
        task: WorkerTask,
        *,
        worktree: WorktreeContext,
        push: bool,
        dry_run: bool,
        commit_sha: str | None,
    ) -> DeliveryReport:
        spec = MergeRequestSpec(
            source_branch=worktree.working_branch,
            target_branch=task.repository.target_branch or self.project.default_target_branch,
            title=task.title,
            description=self._render_merge_request_description(task, commit_sha=commit_sha),
            draft=self.project.delivery.draft_merge_requests,
            labels=self.project.delivery.default_labels or self.policy.delivery.gitlab.labels,
        )
        push_command = self.delivery.build_push_command(spec)
        pushed = False
        push_exit_code = None
        push_stdout = None
        push_stderr = None
        if push and not dry_run and commit_sha:
            result = self.delivery.push_with_merge_request(spec, workdir=str(worktree.path))
            pushed = True
            push_exit_code = result.returncode
            push_stdout = result.stdout
            push_stderr = result.stderr
        return DeliveryReport(
            push_command=push_command,
            api_endpoint=self.project.gitlab.merge_requests_api,
            api_payload=self.delivery.build_api_payload(spec),
            pushed=pushed,
            push_exit_code=push_exit_code,
            push_stdout=push_stdout,
            push_stderr=push_stderr,
            merge_request_url_hint=f"{self.project.gitlab.project_web_url}/-/merge_requests",
        )

    def _render_merge_request_description(self, task: WorkerTask, *, commit_sha: str | None) -> str:
        lines = [
            f"Automated delivery for {task.task_id}.",
            "",
            f"Objective: {task.objective}",
            f"Target branch: {task.repository.target_branch}",
        ]
        if commit_sha:
            lines.append(f"Commit: {commit_sha}")
        lines.append("")
        lines.append("Acceptance criteria:")
        lines.extend(f"- {item}" for item in task.acceptance_criteria)
        return "\n".join(lines)

    def _serialize_worker_result(self, result: WorkerRunResult | None) -> dict[str, Any] | None:
        if result is None:
            return None
        return asdict(result)

    def _slug(self, value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
        normalized = normalized.strip("-").lower()
        return normalized or "task"


def dump_task_run_report(report: TaskRunReport) -> str:
    return json.dumps(_to_jsonable(report), indent=2)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _to_jsonable(val) for key, val in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(val) for key, val in value.items()}
    return value
