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
        self.assertIn("Damon 是一个先规划、后执行、以 PR 为结束态的开发命令行", completed.stdout)
        self.assertIn("Quick start:", completed.stdout)
        self.assertIn("start        交互式规划并生成 run dossier", completed.stdout)
        self.assertNotIn("validate", completed.stdout)


if __name__ == "__main__":
    unittest.main()
