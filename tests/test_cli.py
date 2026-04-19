import os
import subprocess
import sys
import unittest


class CLITests(unittest.TestCase):
    def test_no_args_prints_bilingual_help(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        completed = subprocess.run(
            [sys.executable, "-m", "damon_autocoding"],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("Damon 是一个围绕 Codex 的旁路推进器", completed.stdout)
        self.assertIn("Quick start:", completed.stdout)
        self.assertIn("attach  绑定当前仓库到一个 Codex session", completed.stdout)
        self.assertIn("loop    自动继续推进 N 个 checkpoint", completed.stdout)
        self.assertIn("status  查看当前绑定和最近推进结果", completed.stdout)
        self.assertIn("pr      把当前结果推成 PR", completed.stdout)


if __name__ == "__main__":
    unittest.main()
