import subprocess
import tempfile
import unittest
from pathlib import Path

from damon_autocoding.workspace import GitWorktreeManager


def run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)


class GitWorktreeManagerTests(unittest.TestCase):
    def test_prepare_and_cleanup_worktree_from_local_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            run(["git", "init", "-b", "main"], cwd=repo_root)
            run(["git", "config", "user.name", "Tester"], cwd=repo_root)
            run(["git", "config", "user.email", "tester@example.com"], cwd=repo_root)
            (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
            run(["git", "add", "README.md"], cwd=repo_root)
            run(["git", "commit", "-m", "init"], cwd=repo_root)

            manager = GitWorktreeManager(repo_root)
            worktree_path = repo_root / ".damon" / "worktrees" / "case"
            context = manager.prepare(
                target_branch="main",
                working_branch="damon/test-worktree",
                worktree_path=worktree_path,
                reset=False,
            )

            self.assertTrue(worktree_path.exists())
            self.assertEqual(context.base_ref, "refs/heads/main")
            self.assertEqual(manager.current_branch(worktree_path), "damon/test-worktree")

            manager.remove(worktree_path, force=True)
            self.assertFalse(worktree_path.exists())


if __name__ == "__main__":
    unittest.main()
