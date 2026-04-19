import tempfile
import unittest
from pathlib import Path
import subprocess

from damon_autocoding.models import (
    CommitStrategy,
    DeliveryPolicy,
    EscalationPolicy,
    ExecutionPolicy,
    ExecutionSettings,
    GitLabPolicy,
    GitPolicy,
    PathConstraints,
    PlanningPolicy,
    RepositoryContext,
    TaskDeliverables,
    TaskHandoff,
    TaskInputs,
    TaskPlan,
    VerificationPolicy,
    WorkerTask,
)
from damon_autocoding.project import DeliveryOptions, GitLabProject, ProjectConfig
from damon_autocoding.repo_profile import CommandSpec, RepositoryProfile
from damon_autocoding.task_runner import TaskRunner


class TaskRunnerTests(unittest.TestCase):
    def test_dry_run_prepares_worktree_and_runs_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._init_repo(repo_root)
            profile = RepositoryProfile(
                version="0.1",
                worktree_root=".damon/worktrees",
                lint_commands=[CommandSpec(name="readme-check", command="test -f README.md")],
                test_commands=[CommandSpec(name="pwd-check", command="pwd >/dev/null")],
                run_review=False,
                commit_changes=True,
                commit_message_template="{task_id}: {title}",
            )
            project = ProjectConfig(
                version="0.1",
                name="Example",
                remote_name="origin",
                remote_url="git@example.com:example/test.git",
                default_target_branch="main",
                gitlab=GitLabProject(
                    api_base_url="https://git.example.com/api/v4",
                    web_base_url="https://git.example.com",
                    project_path="example/test",
                ),
                delivery=DeliveryOptions(default_labels=["damon"]),
            )
            policy = ExecutionPolicy(
                version="0.1",
                planning=PlanningPolicy(),
                escalation=EscalationPolicy(),
                delivery=DeliveryPolicy(
                    git=GitPolicy(branch_prefix="damon/", commit_strategy=CommitStrategy.CHECKPOINT, push_after_green=True),
                    gitlab=GitLabPolicy(open_merge_request=True, draft_by_default=False, labels=["damon"]),
                ),
                verification=VerificationPolicy(
                    require_unit_tests=True,
                    require_lint=True,
                    require_static_analysis=False,
                    require_ci_green=False,
                    reviewer_agent_required=False,
                ),
                execution=ExecutionSettings(),
            )
            task = WorkerTask(
                version="0.1",
                task_id="TASK-DRY-RUN",
                title="Dry run task",
                objective="Validate the runner.",
                repository=RepositoryContext(
                    path=".",
                    default_branch="main",
                    working_branch="damon/dry-run-task",
                    target_branch="main",
                ),
                acceptance_criteria=["Checks execute inside an isolated worktree."],
                constraints=PathConstraints(allowed_paths=["**/*"], forbidden_paths=[], max_changed_files=10),
                inputs=TaskInputs(architecture_refs=["docs/architecture.md"], policy_ref="configs/execution_policy.yaml"),
                plan=TaskPlan(parent_goal="Runner validation"),
                deliverables=TaskDeliverables(code_changes_required=False, tests_required=True, expected_outputs=["report"]),
                handoff=TaskHandoff(must_produce=["report"]),
            )

            report = TaskRunner(project=project, policy=policy, profile=profile).run(
                task,
                repo_root=repo_root,
                dry_run=True,
                cleanup=True,
                reset_worktree=True,
            )

            self.assertTrue(report.success)
            self.assertTrue(report.dry_run)
            self.assertEqual(report.worker_result, None)
            self.assertEqual(report.lint_results[0].exit_code, 0)
            self.assertEqual(report.test_results[0].exit_code, 0)
            self.assertEqual(report.delivery.api_payload["source_branch"], "damon/dry-run-task")
            self.assertFalse(Path(report.worktree_path).exists())
            branch_check = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", "refs/heads/damon/dry-run-task"],
                cwd=repo_root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(branch_check.returncode, 0)

    def _init_repo(self, repo_root: Path) -> None:
        subprocess.run(["git", "init", "-b", "main"], cwd=repo_root, text=True, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_root, text=True, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo_root, text=True, capture_output=True, check=True)
        (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo_root, text=True, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo_root, text=True, capture_output=True, check=True)


if __name__ == "__main__":
    unittest.main()
