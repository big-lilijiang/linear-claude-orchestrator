import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from damon_autocoding.planner import DossierDraft, PlannerTurn
from damon_autocoding.runs import (
    PlannerIO,
    RunManager,
    RunStatus,
    StartFlow,
    create_blocked_pr,
    create_complete_pr,
    execute_run,
)
from damon_autocoding.summarizer import PRSummary


class FakePlanner:
    def next_turn(self, *, repo_root, goal, scan_summary, transcript, language_hint):
        user_turns = [message for message in transcript if message.role == "user"]
        if len(user_turns) == 1:
            return PlannerTurn(
                language="en",
                repo_summary="The repository is a small Python project with tests.",
                repo_risks=["None for the fake planner."],
                candidate_features=[],
                recommendation="Build the runner UX first.",
                questions=["Should Damon optimize for UX polish or deeper automation in this run?"],
                ready_for_dossier=False,
                reply_to_user="I recommend focusing on the planning UX first. What should this run optimize for?",
            )
        return PlannerTurn(
            language="en",
            repo_summary="The repository is a small Python project with tests.",
            repo_risks=[],
            candidate_features=[],
            recommendation="Build the runner UX first.",
            questions=[],
            ready_for_dossier=True,
            reply_to_user="I have enough information to draft the dossier.",
        )

    def build_dossier(self, *, repo_root, goal, scan_summary, transcript, language_hint):
        return DossierDraft(
            language="en",
            title="Planner driven task runner",
            goal=goal,
            scope_items=["Improve the planning UX.", "Preserve the current execution chain."],
            non_goals=["No unrelated refactors."],
            architecture_notes=["Route planning through a Codex-backed planner module."],
            allowed_paths=["src/**", "tests/**", "README.md"],
            forbidden_paths=[],
            constraints=["Keep the top-level CLI simple."],
            definition_of_done=["Lint passes.", "Tests pass.", "The CLI can create a dossier."],
            lint_commands=["python3 -m compileall src tests"],
            test_commands=["PYTHONPATH=src python3 -m unittest discover -s tests -v"],
            static_analysis_commands=[],
            target_branch="main",
            base_ref="main",
            working_branch="damon/planner-driven-task-runner",
            run_review=False,
            auto_push_complete_pr=True,
            auto_push_blocked_pr=True,
            draft_merge_request=True,
            summary_for_user="The dossier is ready. It will improve planning UX while keeping execution intact.",
            goal_markdown="# Goal\n\nImprove the planning UX.",
            architecture_markdown="# Architecture\n\nUse a Codex-backed planner.",
            repo_scan_markdown="# Repo Scan\n\nSmall Python repo with tests.",
        )


class FakePRSummaryBackend:
    def __init__(self, *args, **kwargs) -> None:
        self.session_id = "summary-session-123"

    def build_summary(
        self,
        *,
        kind,
        repo_root,
        worktree_path,
        manifest,
        execution_report,
        language_hint,
        session_id,
    ):
        if kind == "blocked":
            return PRSummary(
                language="en",
                title="Blocked: Planner driven task runner",
                description="Blocked PR summary from Codex.",
                blocker_note_markdown="# Blocked\n\nNeed one user decision.",
            )
        return PRSummary(
            language="en",
            title="Planner driven task runner",
            description="Complete PR summary from Codex.",
            blocker_note_markdown="",
        )


class RunFlowTests(unittest.TestCase):
    def test_start_flow_creates_ready_run_and_dossier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._init_repo(repo_root)
            stdin = io.StringIO("Optimize for UX polish.\n\n\n")
            stdout = io.StringIO()

            manifest, paths = StartFlow(planner=FakePlanner()).run(
                repo_root=repo_root,
                goal="Implement a planner-driven task runner",
                io=PlannerIO(stdin=stdin, stdout=stdout),
            )

            self.assertEqual(manifest.status, RunStatus.READY)
            self.assertTrue(paths.manifest_path.exists())
            self.assertTrue(paths.project_path.exists())
            self.assertTrue(paths.profile_path.exists())
            self.assertTrue(paths.task_path.exists())
            self.assertIn("planning UX", stdout.getvalue())
            self.assertGreaterEqual(len(manifest.planning_transcript), 3)

    def test_execute_run_dry_run_uses_latest_dossier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._init_repo(repo_root)
            stdin = io.StringIO("Optimize for UX polish.\n\n\n")
            stdout = io.StringIO()

            manifest, _ = StartFlow(planner=FakePlanner()).run(
                repo_root=repo_root,
                goal="Implement a planner-driven task runner",
                io=PlannerIO(stdin=stdin, stdout=stdout),
            )

            updated_manifest, report = execute_run(
                repo_root=repo_root,
                run_id=manifest.run_id,
                dry_run=True,
                cleanup=True,
                reset_worktree=True,
                worker_timeout_seconds=None,
                review_timeout_seconds=None,
            )

            self.assertEqual(updated_manifest.status, RunStatus.EXECUTED)
            self.assertTrue(report["success"])
            self.assertTrue(report["cleanup_performed"])
            self.assertIsNotNone(updated_manifest.latest_execute_report)
            latest_manifest = RunManager(repo_root).load_manifest(manifest.run_id)
            self.assertEqual(latest_manifest.status, RunStatus.EXECUTED)

    def test_complete_pr_uses_codex_summary_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._init_repo(repo_root)
            subprocess.run(["git", "checkout", "-b", "damon/test-complete-pr"], cwd=repo_root, text=True, capture_output=True, check=True)
            manifest = self._create_manifest(repo_root)
            self._save_execution_report(
                repo_root,
                manifest.run_id,
                {
                    "success": True,
                    "worktree_path": str(repo_root),
                    "working_branch": "damon/test-complete-pr",
                    "base_ref": "main",
                    "cleanup_performed": False,
                    "lint_results": [],
                    "test_results": [],
                    "static_analysis_results": [],
                    "worker_result": {"session_id": "worker-session-1"},
                    "review_result": None,
                    "commit_sha": None,
                },
            )

            with patch("damon_autocoding.runs.CodexPRSummaryBackend", FakePRSummaryBackend), patch(
                "damon_autocoding.gitlab.GitLabDelivery.push_with_merge_request"
            ) as push_mock:
                push_mock.return_value = subprocess.CompletedProcess(["git", "push"], 0, "ok", "")
                payload = create_complete_pr(repo_root=repo_root, run_id=manifest.run_id)

            self.assertEqual(payload["merge_request_spec"]["title"], "Draft: Planner driven task runner")
            self.assertIn("Complete PR summary from Codex.", payload["merge_request_spec"]["description"])
            latest_manifest = RunManager(repo_root).load_manifest(manifest.run_id)
            self.assertEqual(latest_manifest.delivery_session_id, "summary-session-123")

    def test_blocked_pr_uses_codex_summary_backend_and_writes_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._init_repo(repo_root)
            subprocess.run(["git", "checkout", "-b", "damon/test-blocked-pr"], cwd=repo_root, text=True, capture_output=True, check=True)
            manifest = self._create_manifest(repo_root)
            self._save_execution_report(
                repo_root,
                manifest.run_id,
                {
                    "success": False,
                    "worktree_path": str(repo_root),
                    "working_branch": "damon/test-blocked-pr",
                    "base_ref": "main",
                    "cleanup_performed": False,
                    "lint_results": [{"name": "lint-1", "required": True, "exit_code": 1}],
                    "test_results": [],
                    "static_analysis_results": [],
                    "worker_result": {"session_id": "worker-session-2", "exit_code": 1},
                    "review_result": None,
                    "commit_sha": None,
                },
            )

            with patch("damon_autocoding.runs.CodexPRSummaryBackend", FakePRSummaryBackend), patch(
                "damon_autocoding.gitlab.GitLabDelivery.push_with_merge_request"
            ) as push_mock:
                push_mock.return_value = subprocess.CompletedProcess(["git", "push"], 0, "ok", "")
                payload = create_blocked_pr(repo_root=repo_root, run_id=manifest.run_id)

            blocker_path = repo_root / ".damon" / "blocked" / f"{manifest.run_id}.md"
            self.assertTrue(blocker_path.exists())
            self.assertIn("Need one user decision.", blocker_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["merge_request_spec"]["title"], "Draft: Blocked: Planner driven task runner")
            self.assertIn("Blocked PR summary from Codex.", payload["merge_request_spec"]["description"])

    def _init_repo(self, repo_root: Path) -> None:
        subprocess.run(["git", "init", "-b", "main"], cwd=repo_root, text=True, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_root, text=True, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo_root, text=True, capture_output=True, check=True)
        (repo_root / "pyproject.toml").write_text("[project]\nname='example'\nversion='0.1.0'\n", encoding="utf-8")
        (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
        (repo_root / "src").mkdir()
        (repo_root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (repo_root / "tests").mkdir()
        (repo_root / "tests" / "test_basic.py").write_text(
            "import unittest\n\n\nclass BasicTest(unittest.TestCase):\n    def test_truth(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=repo_root, text=True, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo_root, text=True, capture_output=True, check=True)

    def _create_manifest(self, repo_root: Path):
        stdin = io.StringIO("Optimize for UX polish.\n\n\n")
        stdout = io.StringIO()
        manifest, _ = StartFlow(planner=FakePlanner()).run(
            repo_root=repo_root,
            goal="Implement a planner-driven task runner",
            io=PlannerIO(stdin=stdin, stdout=stdout),
        )
        return manifest

    def _save_execution_report(self, repo_root: Path, run_id: str, payload: dict) -> None:
        manager = RunManager(repo_root)
        report_path = manager.save_report(run_id, "execute-latest", payload)
        manifest = manager.load_manifest(run_id)
        manifest.latest_execute_report = str(report_path.relative_to(manager.repo_root))
        manifest.execution_session_id = (payload.get("worker_result") or {}).get("session_id")
        manager.save_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
