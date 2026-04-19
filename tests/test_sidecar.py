import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from damon_autocoding.sidecar import (
    CodexSessionRef,
    CodexSessionRegistry,
    LoopStepRecord,
    LoopResult,
    SidecarSupervisor,
    utcnow,
)


class FakeRegistry(CodexSessionRegistry):
    def __init__(self, session: CodexSessionRef) -> None:
        self._session = session

    def resolve(self, *, repo_root, session_id=None):
        return self._session


class FakeBackend:
    def __init__(self) -> None:
        self.loop_calls = 0

    def continue_step(
        self,
        *,
        session_id,
        repo_root,
        working_branch,
        target_branch,
        step_index,
        previous_steps,
        extra_instruction=None,
    ):
        self.loop_calls += 1
        status = "blocked" if self.loop_calls == 2 else "checkpoint"
        return (
            LoopResult(
                status=status,
                summary=f"step {self.loop_calls}",
                next_action="continue",
                files_touched=["README.md"],
                tests_run=["pytest"],
                tests_green=True,
                blocker="Need input" if status == "blocked" else None,
            ),
            session_id,
        )

    def summarize_pr(self, *, session_id, repo_root, kind, state, git_status):
        from damon_autocoding.sidecar import PRSummary

        note = "# Blocked\n\nNeed input." if kind == "blocked" else ""
        title = "Blocked: Example change" if kind == "blocked" else "Example change"
        return PRSummary(title=title, description=f"{kind} summary", blocker_note_markdown=note), session_id


class SidecarTests(unittest.TestCase):
    def test_attach_creates_sidecar_branch_when_on_target_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._init_repo(repo_root)
            session = CodexSessionRef(
                session_id="session-1",
                session_file="/tmp/session.jsonl",
                cwd=str(repo_root),
                started_at="2026-04-19T00:00:00",
            )
            supervisor = SidecarSupervisor(repo_root=repo_root, registry=FakeRegistry(session), backend=FakeBackend())
            state = supervisor.attach()
            self.assertTrue(state.working_branch.startswith("damon/loop-"))
            self.assertTrue((repo_root / ".damon" / "sidecar.yaml").exists())

    def test_loop_updates_history_and_stops_on_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._init_repo(repo_root)
            session = CodexSessionRef(
                session_id="session-1",
                session_file="/tmp/session.jsonl",
                cwd=str(repo_root),
                started_at="2026-04-19T00:00:00",
            )
            backend = FakeBackend()
            supervisor = SidecarSupervisor(repo_root=repo_root, registry=FakeRegistry(session), backend=backend)
            supervisor.attach()
            state = supervisor.loop(steps=5)
            self.assertEqual(len(state.history), 2)
            self.assertEqual(state.latest_status, "blocked")
            self.assertEqual(state.history[-1].blocker, "Need input")

    def test_pr_uses_current_branch_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._init_repo(repo_root)
            session = CodexSessionRef(
                session_id="session-1",
                session_file="/tmp/session.jsonl",
                cwd=str(repo_root),
                started_at="2026-04-19T00:00:00",
            )
            backend = FakeBackend()
            supervisor = SidecarSupervisor(repo_root=repo_root, registry=FakeRegistry(session), backend=backend)
            state = supervisor.attach()
            state.history.append(
                LoopStepRecord(
                    step_index=1,
                    started_at=utcnow(),
                    finished_at=utcnow(),
                    status="blocked",
                    summary="blocked step",
                    next_action="need input",
                    files_touched=["README.md"],
                    tests_run=[],
                    tests_green=None,
                    blocker="Need input",
                    raw_output="{}",
                    session_id=state.session.session_id,
                )
            )
            state.latest_status = "blocked"
            supervisor.save_state(state)

            with patch("damon_autocoding.gitlab.GitLabDelivery.push_with_merge_request") as push_mock:
                push_mock.return_value = subprocess.CompletedProcess(["git", "push"], 0, "ok", "")
                payload = supervisor.open_pr(kind="blocked")

            self.assertEqual(payload["title"], "Blocked: Example change")
            self.assertTrue((repo_root / ".damon" / "reports" / "pr-blocked.json").exists())
            self.assertTrue((repo_root / ".damon" / "blocked.md").exists())

    def _init_repo(self, repo_root: Path) -> None:
        subprocess.run(["git", "init", "-b", "main"], cwd=repo_root, text=True, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_root, text=True, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo_root, text=True, capture_output=True, check=True)
        subprocess.run(["git", "remote", "add", "origin", "git@gitlab.kidinsight.cn:autocoding/autoengine.git"], cwd=repo_root, text=True, capture_output=True, check=True)
        (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo_root, text=True, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo_root, text=True, capture_output=True, check=True)


if __name__ == "__main__":
    unittest.main()
