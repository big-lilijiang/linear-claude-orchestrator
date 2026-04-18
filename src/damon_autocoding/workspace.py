from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _coerce_process_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


@dataclass(slots=True)
class CommandExecutionResult:
    name: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    required: bool = True
    timed_out: bool = False


@dataclass(slots=True)
class WorktreeContext:
    path: Path
    working_branch: str
    base_ref: str


def run_shell_command(
    *,
    name: str,
    command: str,
    cwd: str | Path,
    required: bool = True,
    timeout_seconds: int | None = None,
) -> CommandExecutionResult:
    try:
        completed = subprocess.run(
            ["zsh", "-lc", command],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        return CommandExecutionResult(
            name=name,
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            required=required,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandExecutionResult(
            name=name,
            command=command,
            exit_code=124,
            stdout=_coerce_process_stream(exc.stdout),
            stderr=_coerce_process_stream(exc.stderr) + "\nCommand timed out.",
            required=required,
            timed_out=True,
        )


class GitWorktreeManager:
    def __init__(self, repo_root: str | Path, *, remote_name: str = "origin") -> None:
        self.repo_root = Path(repo_root).resolve()
        self.remote_name = remote_name

    def prepare(
        self,
        *,
        target_branch: str,
        working_branch: str,
        worktree_path: str | Path,
        base_ref: str | None = None,
        reset: bool = False,
    ) -> WorktreeContext:
        self._assert_git_repository()
        worktree_path = Path(worktree_path).resolve()
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        if worktree_path.exists():
            if not reset:
                raise FileExistsError(f"worktree path already exists: {worktree_path}")
            self.remove(worktree_path, force=True, missing_ok=True)
        resolved_base_ref = self.resolve_base_ref(target_branch, preferred_ref=base_ref)
        self._run_git(["worktree", "add", "--force", "--detach", str(worktree_path), resolved_base_ref], cwd=self.repo_root)
        self._run_git(["checkout", "-B", working_branch, resolved_base_ref], cwd=worktree_path)
        return WorktreeContext(path=worktree_path, working_branch=working_branch, base_ref=resolved_base_ref)

    def remove(self, worktree_path: str | Path, *, force: bool = False, missing_ok: bool = False) -> None:
        worktree_path = Path(worktree_path).resolve()
        if not worktree_path.exists() and missing_ok:
            return
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(worktree_path))
        completed = self._run_git(args, cwd=self.repo_root, check=False)
        if completed.returncode != 0 and worktree_path.exists():
            shutil.rmtree(worktree_path)

    def resolve_base_ref(self, target_branch: str, *, preferred_ref: str | None = None) -> str:
        if preferred_ref:
            resolved = self._resolve_explicit_ref(preferred_ref)
            if resolved:
                return resolved
        if self._remote_exists():
            self._run_git(["fetch", self.remote_name, target_branch], cwd=self.repo_root, check=False)
            remote_ref = f"refs/remotes/{self.remote_name}/{target_branch}"
            if self._ref_exists(remote_ref):
                return remote_ref
        local_ref = f"refs/heads/{target_branch}"
        if self._ref_exists(local_ref):
            return local_ref
        raise RuntimeError(f"Unable to resolve base ref for branch {target_branch}")

    def _resolve_explicit_ref(self, ref: str) -> str | None:
        candidates = [ref, f"refs/heads/{ref}", f"refs/remotes/{self.remote_name}/{ref}", f"refs/tags/{ref}"]
        for candidate in candidates:
            completed = self._run_git(["rev-parse", "--verify", "--quiet", candidate], cwd=self.repo_root, check=False)
            if completed.returncode == 0:
                return candidate
        return None

    def has_uncommitted_changes(self, worktree_path: str | Path) -> bool:
        completed = self._run_git(["status", "--porcelain"], cwd=worktree_path)
        return bool(completed.stdout.strip())

    def commit_all(self, worktree_path: str | Path, *, message: str) -> str:
        self._run_git(["add", "-A"], cwd=worktree_path)
        self._run_git(["commit", "-m", message], cwd=worktree_path)
        return self.current_head(worktree_path)

    def current_head(self, worktree_path: str | Path) -> str:
        completed = self._run_git(["rev-parse", "HEAD"], cwd=worktree_path)
        return completed.stdout.strip()

    def current_branch(self, worktree_path: str | Path) -> str:
        completed = self._run_git(["branch", "--show-current"], cwd=worktree_path)
        return completed.stdout.strip()

    def _assert_git_repository(self) -> None:
        completed = self._run_git(["rev-parse", "--git-dir"], cwd=self.repo_root, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"{self.repo_root} is not a git repository")

    def _remote_exists(self) -> bool:
        completed = self._run_git(["remote"], cwd=self.repo_root)
        remotes = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
        return self.remote_name in remotes

    def _ref_exists(self, ref: str) -> bool:
        completed = self._run_git(["show-ref", "--verify", "--quiet", ref], cwd=self.repo_root, check=False)
        return completed.returncode == 0

    def _run_git(
        self,
        args: list[str],
        *,
        cwd: str | Path,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"git {' '.join(args)} failed")
        return completed
