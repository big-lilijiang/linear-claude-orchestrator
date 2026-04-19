from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .config import load_model, save_model
from .gitlab import GitLabDelivery, MergeRequestSpec
from .project import DeliveryOptions, GitLabProject, ProjectConfig


LoopStatus = Literal["checkpoint", "done", "blocked", "error"]


class CodexSessionRef(BaseModel):
    session_id: str
    session_file: str
    cwd: str
    started_at: str | None = None


class LoopStepRecord(BaseModel):
    step_index: int
    started_at: str
    finished_at: str
    status: LoopStatus
    summary: str
    next_action: str
    files_touched: list[str] = Field(default_factory=list)
    tests_run: list[str] = Field(default_factory=list)
    tests_green: bool | None = None
    blocker: str | None = None
    raw_output: str = ""
    session_id: str | None = None


class SupervisorState(BaseModel):
    version: str = "0.1"
    repo_root: str
    attached_at: str
    session: CodexSessionRef
    remote_name: str
    remote_url: str
    target_branch: str
    working_branch: str
    latest_status: LoopStatus | None = None
    latest_summary: str | None = None
    latest_pr_report: str | None = None
    history: list[LoopStepRecord] = Field(default_factory=list)


class LoopResult(BaseModel):
    status: LoopStatus
    summary: str
    next_action: str
    files_touched: list[str] = Field(default_factory=list)
    tests_run: list[str] = Field(default_factory=list)
    tests_green: bool | None = None
    blocker: str | None = None


class PRSummary(BaseModel):
    title: str
    description: str
    blocker_note_markdown: str = ""


@dataclass(slots=True)
class SidecarPaths:
    repo_root: Path

    @property
    def root(self) -> Path:
        return self.repo_root / ".damon"

    @property
    def state_path(self) -> Path:
        return self.root / "sidecar.yaml"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"


@dataclass(slots=True)
class RepoSnapshot:
    repo_root: Path
    remote_name: str
    remote_url: str
    current_branch: str
    target_branch: str
    dirty: bool


class CodexSessionRegistry:
    def __init__(self, *, sessions_root: str | Path | None = None) -> None:
        self.sessions_root = Path(sessions_root or Path.home() / ".codex" / "sessions").expanduser()

    def resolve(self, *, repo_root: str | Path, session_id: str | None = None) -> CodexSessionRef:
        repo_root = Path(repo_root).resolve()
        session_files = sorted(self.sessions_root.rglob("*.jsonl"), reverse=True)
        if session_id:
            for path in session_files:
                meta = self._read_session_meta(path)
                if meta and meta.session_id == session_id:
                    return meta
            raise FileNotFoundError(f"Codex session not found: {session_id}")

        for path in session_files:
            meta = self._read_session_meta(path)
            if not meta:
                continue
            session_cwd = Path(meta.cwd).resolve()
            try:
                if session_cwd == repo_root or session_cwd.is_relative_to(repo_root):
                    return meta
            except ValueError:
                continue
        raise FileNotFoundError(
            f"No Codex session found for repo {repo_root}. Start Codex in this repo first or pass --session."
        )

    def _read_session_meta(self, path: Path) -> CodexSessionRef | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                first_line = handle.readline()
        except OSError:
            return None
        if not first_line:
            return None
        try:
            payload = json.loads(first_line)
        except json.JSONDecodeError:
            return None
        if payload.get("type") != "session_meta":
            return None
        meta = payload.get("payload", {})
        session_id = meta.get("id")
        cwd = meta.get("cwd")
        if not session_id or not cwd:
            return None
        return CodexSessionRef(
            session_id=session_id,
            session_file=str(path),
            cwd=cwd,
            started_at=meta.get("timestamp"),
        )


class SidecarBackend(Protocol):
    def continue_step(
        self,
        *,
        session_id: str,
        repo_root: str | Path,
        working_branch: str,
        target_branch: str,
        step_index: int,
        previous_steps: list[LoopStepRecord],
        extra_instruction: str | None = None,
    ) -> tuple[LoopResult, str | None]:
        ...

    def summarize_pr(
        self,
        *,
        session_id: str,
        repo_root: str | Path,
        kind: Literal["complete", "blocked"],
        state: SupervisorState,
        git_status: str,
    ) -> tuple[PRSummary, str | None]:
        ...


class CodexSidecarBackend:
    def __init__(self, *, reasoning_effort: str = "xhigh", timeout_seconds: int = 1800) -> None:
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds

    def continue_step(
        self,
        *,
        session_id: str,
        repo_root: str | Path,
        working_branch: str,
        target_branch: str,
        step_index: int,
        previous_steps: list[LoopStepRecord],
        extra_instruction: str | None = None,
    ) -> tuple[LoopResult, str | None]:
        prompt = build_loop_prompt(
            working_branch=working_branch,
            target_branch=target_branch,
            step_index=step_index,
            previous_steps=previous_steps,
            extra_instruction=extra_instruction,
        )
        payload, resolved_session = self._run_resume_json(
            session_id=session_id,
            repo_root=repo_root,
            prompt=prompt,
        )
        return LoopResult.model_validate(payload), resolved_session

    def summarize_pr(
        self,
        *,
        session_id: str,
        repo_root: str | Path,
        kind: Literal["complete", "blocked"],
        state: SupervisorState,
        git_status: str,
    ) -> tuple[PRSummary, str | None]:
        prompt = build_pr_prompt(kind=kind, state=state, git_status=git_status)
        payload, resolved_session = self._run_resume_json(
            session_id=session_id,
            repo_root=repo_root,
            prompt=prompt,
        )
        return PRSummary.model_validate(payload), resolved_session

    def _run_resume_json(self, *, session_id: str, repo_root: str | Path, prompt: str) -> tuple[dict, str | None]:
        with tempfile.TemporaryDirectory(prefix="damon-loop-") as temp_dir:
            output_path = Path(temp_dir) / "out.json"
            command = [
                "codex",
                "exec",
                "resume",
                session_id,
                "-c",
                f'model_reasoning_effort="{self.reasoning_effort}"',
                "-o",
                str(output_path),
                prompt,
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=repo_root,
                    text=True,
                    capture_output=True,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                stderr = _coerce_stream(exc.stderr)
                raise RuntimeError(f"Codex sidecar step timed out.\n{stderr}".strip()) from exc

            if completed.returncode != 0:
                raise RuntimeError(
                    "Codex sidecar step failed.\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            if not output_path.exists():
                raise RuntimeError("Codex sidecar step did not produce output.")
            return parse_json_output(output_path.read_text(encoding="utf-8")), extract_session_id(completed.stderr)


class SidecarSupervisor:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        registry: CodexSessionRegistry | None = None,
        backend: SidecarBackend | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.paths = SidecarPaths(self.repo_root)
        self.registry = registry or CodexSessionRegistry()
        self.backend = backend or CodexSidecarBackend()

    def attach(self, *, session_id: str | None = None) -> SupervisorState:
        session = self.registry.resolve(repo_root=self.repo_root, session_id=session_id)
        snapshot = inspect_repo(self.repo_root)
        working_branch = snapshot.current_branch
        if not working_branch or working_branch == snapshot.target_branch:
            working_branch = create_sidecar_branch(self.repo_root)
            snapshot = inspect_repo(self.repo_root)
        state = SupervisorState(
            repo_root=str(self.repo_root),
            attached_at=utcnow(),
            session=session,
            remote_name=snapshot.remote_name,
            remote_url=snapshot.remote_url,
            target_branch=snapshot.target_branch,
            working_branch=working_branch,
        )
        self.save_state(state)
        return state

    def loop(self, *, steps: int, extra_instruction: str | None = None) -> SupervisorState:
        state = self.load_state()
        for _ in range(steps):
            step_index = len(state.history) + 1
            result, resolved_session = self.backend.continue_step(
                session_id=state.session.session_id,
                repo_root=self.repo_root,
                working_branch=state.working_branch,
                target_branch=state.target_branch,
                step_index=step_index,
                previous_steps=state.history[-5:],
                extra_instruction=extra_instruction,
            )
            record = LoopStepRecord(
                step_index=step_index,
                started_at=utcnow(),
                finished_at=utcnow(),
                status=result.status,
                summary=result.summary,
                next_action=result.next_action,
                files_touched=result.files_touched,
                tests_run=result.tests_run,
                tests_green=result.tests_green,
                blocker=result.blocker,
                raw_output=json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                session_id=resolved_session or state.session.session_id,
            )
            state.history.append(record)
            state.latest_status = record.status
            state.latest_summary = record.summary
            if resolved_session:
                state.session.session_id = resolved_session
            self.save_state(state)
            if record.status in {"done", "blocked"}:
                break
        return state

    def status(self) -> tuple[SupervisorState, RepoSnapshot]:
        state = self.load_state()
        snapshot = inspect_repo(self.repo_root)
        return state, snapshot

    def open_pr(self, *, kind: Literal["auto", "complete", "blocked"] = "auto") -> dict:
        state = self.load_state()
        snapshot = inspect_repo(self.repo_root)
        resolved_kind = kind
        if resolved_kind == "auto":
            resolved_kind = "blocked" if state.latest_status == "blocked" else "complete"
        if resolved_kind not in {"complete", "blocked"}:
            raise ValueError(f"Unknown PR kind: {kind}")

        git_status = git_output(self.repo_root, ["status", "--short", "--branch"], check=False)
        summary, resolved_session = self.backend.summarize_pr(
            session_id=state.session.session_id,
            repo_root=self.repo_root,
            kind=resolved_kind,  # type: ignore[arg-type]
            state=state,
            git_status=git_status,
        )
        if resolved_session:
            state.session.session_id = resolved_session

        if resolved_kind == "blocked" and summary.blocker_note_markdown.strip():
            blocker_path = self.paths.root / "blocked.md"
            blocker_path.parent.mkdir(parents=True, exist_ok=True)
            blocker_path.write_text(summary.blocker_note_markdown, encoding="utf-8")

        commit_if_needed(self.repo_root, message=_pr_commit_message(resolved_kind))
        project = infer_project_config(self.repo_root, snapshot)
        delivery = GitLabDelivery(project)
        spec = MergeRequestSpec(
            source_branch=snapshot.current_branch,
            target_branch=snapshot.target_branch,
            title=summary.title,
            description=summary.description,
            draft=False,
            labels=project.delivery.default_labels,
        )
        result = delivery.push_with_merge_request(spec, workdir=str(self.repo_root))
        report = {
            "kind": resolved_kind,
            "source_branch": spec.source_branch,
            "target_branch": spec.target_branch,
            "title": spec.title,
            "description": spec.description,
            "push_command": delivery.build_push_command(spec),
            "push_exit_code": result.returncode,
            "push_stdout": result.stdout,
            "push_stderr": result.stderr,
            "merge_request_url_hint": f"{project.gitlab.project_web_url}/-/merge_requests",
            "session_id": state.session.session_id,
        }
        self.paths.reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.paths.reports_dir / f"pr-{resolved_kind}.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        state.latest_pr_report = str(report_path.relative_to(self.repo_root))
        self.save_state(state)
        return report

    def load_state(self) -> SupervisorState:
        if not self.paths.state_path.exists():
            raise FileNotFoundError("No sidecar state found. Run `damon attach` first.")
        return load_model(self.paths.state_path, SupervisorState)

    def save_state(self, state: SupervisorState) -> None:
        save_model(self.paths.state_path, state)


def inspect_repo(repo_root: str | Path) -> RepoSnapshot:
    repo_root = Path(repo_root).resolve()
    remote_name = "origin"
    remote_url = git_output(repo_root, ["remote", "get-url", remote_name], check=False)
    current_branch = git_output(repo_root, ["branch", "--show-current"], check=False) or ""
    target_branch = detect_default_branch(repo_root) or "main"
    dirty = bool(git_output(repo_root, ["status", "--porcelain"], check=False).strip())
    return RepoSnapshot(
        repo_root=repo_root,
        remote_name=remote_name,
        remote_url=remote_url,
        current_branch=current_branch,
        target_branch=target_branch,
        dirty=dirty,
    )


def detect_default_branch(repo_root: str | Path) -> str | None:
    repo_root = Path(repo_root).resolve()
    ref = git_output(repo_root, ["symbolic-ref", "refs/remotes/origin/HEAD"], check=False)
    if ref and "/" in ref:
        return ref.rsplit("/", 1)[-1]
    for candidate in ("refs/remotes/origin/main", "refs/heads/main", "refs/remotes/origin/master", "refs/heads/master"):
        if git_returncode(repo_root, ["show-ref", "--verify", "--quiet", candidate]) == 0:
            return candidate.rsplit("/", 1)[-1]
    return None


def create_sidecar_branch(repo_root: str | Path) -> str:
    repo_root = Path(repo_root).resolve()
    branch_name = f"damon/loop-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    git_output(repo_root, ["checkout", "-b", branch_name])
    return branch_name


def commit_if_needed(repo_root: str | Path, *, message: str) -> None:
    repo_root = Path(repo_root).resolve()
    if not git_output(repo_root, ["status", "--porcelain"], check=False).strip():
        return
    git_output(repo_root, ["add", "-A"])
    git_output(repo_root, ["commit", "-m", message])


def infer_project_config(repo_root: str | Path, snapshot: RepoSnapshot) -> ProjectConfig:
    web_base_url, project_path = derive_web_info(snapshot.remote_url)
    return ProjectConfig(
        version="0.1",
        name=Path(repo_root).resolve().name,
        remote_name=snapshot.remote_name,
        remote_url=snapshot.remote_url,
        default_target_branch=snapshot.target_branch,
        gitlab=GitLabProject(
            api_base_url=f"{web_base_url}/api/v4",
            web_base_url=web_base_url,
            project_path=project_path,
        ),
        delivery=DeliveryOptions(
            use_push_options=True,
            draft_merge_requests=False,
            default_labels=["damon"],
        ),
    )


def build_loop_prompt(
    *,
    working_branch: str,
    target_branch: str,
    step_index: int,
    previous_steps: list[LoopStepRecord],
    extra_instruction: str | None = None,
) -> str:
    history = [
        {
            "step_index": step.step_index,
            "status": step.status,
            "summary": step.summary,
            "next_action": step.next_action,
        }
        for step in previous_steps
    ]
    prompt = f"""Continue the existing work in this Codex session.

Use the repository's current state and any local plan or architecture files as the source of truth.
Do not restart planning from scratch.
Work on branch {working_branch} targeting {target_branch}.

This is loop step {step_index}. Previous step summaries:
{json.dumps(history, ensure_ascii=False, indent=2)}

Instructions:
- Make as much concrete progress as you can until one natural checkpoint, full completion, or a hard blocker.
- Do not stop for intermediate confirmation unless a true blocker requires a human decision.
- Run the most relevant validation you can when appropriate.

Return ONLY raw JSON with this exact shape:
{{
  "status": "checkpoint|done|blocked",
  "summary": "what you completed in this step",
  "next_action": "the next thing to do",
  "files_touched": ["path1", "path2"],
  "tests_run": ["command1"],
  "tests_green": true,
  "blocker": ""
}}
"""
    if extra_instruction:
        prompt += f"\nAdditional operator instruction:\n{extra_instruction}\n"
    return prompt


def build_pr_prompt(*, kind: Literal["complete", "blocked"], state: SupervisorState, git_status: str) -> str:
    history = [step.model_dump(mode="json") for step in state.history[-10:]]
    return f"""Prepare a {kind} merge request summary for the current repository state.

Repository status:
{git_status}

Supervisor state:
{json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2)}

Recent loop history:
{json.dumps(history, ensure_ascii=False, indent=2)}

Return ONLY raw JSON with this exact shape:
{{
  "title": "merge request title",
  "description": "polished merge request description",
  "blocker_note_markdown": "markdown note for blocked case or empty string"
}}

Rules:
- For complete, describe what changed, validation evidence, and residual risk.
- For blocked, describe what was completed, why it is blocked, and what decision or fix is required next.
- Do not prefix the title with Draft.
"""


def git_output(repo_root: str | Path, args: list[str], *, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"git {' '.join(args)} failed")
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def git_returncode(repo_root: str | Path, args: list[str]) -> int:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode


def utcnow() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def derive_web_info(remote_url: str) -> tuple[str, str]:
    normalized = remote_url.removesuffix(".git")
    if normalized.startswith("git@") and ":" in normalized:
        host = normalized.split("@", 1)[1].split(":", 1)[0]
        project_path = normalized.split(":", 1)[1]
        return f"http://{host}", project_path
    if normalized.startswith("http://") or normalized.startswith("https://"):
        parts = normalized.split("/", 3)
        web_base = "/".join(parts[:3])
        project_path = parts[3] if len(parts) > 3 else "example/project"
        return web_base, project_path
    return "http://example.com", "example/project"


def parse_json_output(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        raise RuntimeError("Structured codex output was empty.")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise RuntimeError(f"Could not parse JSON from codex output:\n{stripped}")
        return json.loads(match.group(0))


def extract_session_id(stderr: str) -> str | None:
    match = re.search(r"session id:\s*([0-9a-f-]+)", stderr or "", re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _coerce_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _pr_commit_message(kind: str) -> str:
    return "damon: prepare blocked pr" if kind == "blocked" else "damon: prepare pr"
