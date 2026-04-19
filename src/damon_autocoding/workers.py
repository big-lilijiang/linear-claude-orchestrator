from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import ExecutionPolicy, WorkerTask


@dataclass(slots=True)
class WorkerRunResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    final_message: str | None
    session_id: str | None = None


def _coerce_process_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def render_task_prompt(task: WorkerTask, policy: ExecutionPolicy) -> str:
    acceptance = "\n".join(f"- {item}" for item in task.acceptance_criteria)
    allowed_paths = "\n".join(f"- {item}" for item in task.constraints.allowed_paths) or "- None"
    forbidden_paths = "\n".join(f"- {item}" for item in task.constraints.forbidden_paths) or "- None"
    must_produce = "\n".join(f"- {item}" for item in task.handoff.must_produce) or "- None"
    return f"""You are executing a bounded software task.

Task ID: {task.task_id}
Title: {task.title}
Objective: {task.objective}
Working branch: {task.repository.working_branch}
Target branch: {task.repository.target_branch}
Estimated complexity: {task.plan.estimated_complexity.value}

Acceptance criteria:
{acceptance}

Allowed paths:
{allowed_paths}

Forbidden paths:
{forbidden_paths}

Required outputs:
{must_produce}

Execution policy:
- Autonomy mode: {policy.autonomy_mode.value}
- Silent replans allowed: {policy.allow_silent_replans}
- Retry budget per stage: {policy.escalation.retry_budget_per_stage}
- Consecutive failure limit: {policy.escalation.consecutive_failure_limit}
- Required lint: {policy.verification.require_lint}
- Required unit tests: {policy.verification.require_unit_tests}
- Required CI green: {policy.verification.require_ci_green}

Instructions:
- Work only within the allowed paths unless blocked by repository reality.
- Do not modify forbidden paths.
- Run relevant validation commands before finishing.
- Summarize risks, tests run, and any remaining follow-ups in the final message.
"""


class CodexCLIWorker:
    def __init__(self, policy: ExecutionPolicy) -> None:
        self.policy = policy
        self.default_timeout_seconds = self.policy.execution.default_timeout_minutes * 60

    def run(
        self,
        task: WorkerTask,
        *,
        workdir: str,
        output_path: str | None = None,
        timeout_seconds: int | None = None,
        session_id: str | None = None,
    ) -> WorkerRunResult:
        prompt = render_task_prompt(task, self.policy)
        if output_path:
            final_message_path = Path(output_path)
        else:
            fd, path = tempfile.mkstemp(prefix="damon-codex-", suffix=".txt")
            os.close(fd)
            final_message_path = Path(path)
        command = self._build_exec_command(
            workdir=workdir,
            final_message_path=final_message_path,
            prompt=prompt,
            session_id=session_id,
        )
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=timeout_seconds or self.default_timeout_seconds,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = _coerce_process_stream(exc.stdout)
            stderr = _coerce_process_stream(exc.stderr) + "\nCodex worker timed out."
        final_message = final_message_path.read_text(encoding="utf-8").strip() if final_message_path.exists() else None
        return WorkerRunResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            final_message=final_message,
            session_id=self._extract_session_id(stderr) if stderr else None,
        )

    def review(self, *, workdir: str, base_branch: str, timeout_seconds: int | None = None) -> WorkerRunResult:
        command = ["codex", "review", "--base", base_branch]
        try:
            completed = subprocess.run(
                command,
                cwd=workdir,
                text=True,
                capture_output=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=timeout_seconds or self.default_timeout_seconds,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = _coerce_process_stream(exc.stdout)
            stderr = _coerce_process_stream(exc.stderr) + "\nCodex review timed out."
        return WorkerRunResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            final_message=stdout.strip() or None,
            session_id=self._extract_session_id(stderr) if stderr else None,
        )

    def _build_exec_command(
        self,
        *,
        workdir: str,
        final_message_path: Path,
        prompt: str,
        session_id: str | None,
    ) -> list[str]:
        if session_id:
            return [
                "codex",
                "exec",
                "resume",
                session_id,
                "-o",
                str(final_message_path),
                prompt,
            ]
        return [
            "codex",
            "exec",
            "--cd",
            workdir,
            "--sandbox",
            self.policy.execution.codex.sandbox_mode,
            "-o",
            str(final_message_path),
            prompt,
        ]

    def _extract_session_id(self, stderr: str) -> str | None:
        match = re.search(r"session id:\s*([0-9a-f-]+)", stderr, re.IGNORECASE)
        if match:
            return match.group(1)
        return None


def dump_worker_result(result: WorkerRunResult) -> str:
    return json.dumps(asdict(result), indent=2)
