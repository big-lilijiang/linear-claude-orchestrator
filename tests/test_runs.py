import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from damon_autocoding.runs import PlannerIO, RunManager, RunStatus, StartFlow, execute_run


class RunFlowTests(unittest.TestCase):
    def test_start_flow_creates_ready_run_and_dossier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._init_repo(repo_root)
            stdin = io.StringIO("\n" * 17 + "\n\n")
            stdout = io.StringIO()

            manifest, paths = StartFlow().run(
                repo_root=repo_root,
                goal="Implement a planner-driven task runner",
                io=PlannerIO(stdin=stdin, stdout=stdout),
            )

            self.assertEqual(manifest.status, RunStatus.READY)
            self.assertTrue(paths.manifest_path.exists())
            self.assertTrue(paths.project_path.exists())
            self.assertTrue(paths.profile_path.exists())
            self.assertTrue(paths.task_path.exists())
            self.assertIn("Repository Scan", stdout.getvalue())

    def test_execute_run_dry_run_uses_latest_dossier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._init_repo(repo_root)
            stdin = io.StringIO("\n" * 17 + "\n\n")
            stdout = io.StringIO()

            manifest, _ = StartFlow().run(
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


if __name__ == "__main__":
    unittest.main()
