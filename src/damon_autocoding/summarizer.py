from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class PRSummary(BaseModel):
    language: str = "en"
    title: str
    description: str
    blocker_note_markdown: str = ""


class PRSummaryBackend(Protocol):
    def build_summary(
        self,
        *,
        kind: str,
        repo_root: str | Path,
        worktree_path: str | Path,
        manifest: dict,
        execution_report: dict,
        language_hint: str,
        session_id: str | None,
    ) -> PRSummary:
        ...


class CodexPRSummaryBackend:
    def __init__(
        self,
        *,
        timeout_seconds: int = 900,
        sandbox_mode: str = "read-only",
        reasoning_effort: str = "xhigh",
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.sandbox_mode = sandbox_mode
        self.reasoning_effort = reasoning_effort
        self.session_id: str | None = None

    def build_summary(
        self,
        *,
        kind: str,
        repo_root: str | Path,
        worktree_path: str | Path,
        manifest: dict,
        execution_report: dict,
        language_hint: str,
        session_id: str | None,
    ) -> PRSummary:
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["zh", "en"]},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "blocker_note_markdown": {"type": "string"},
            },
            "required": ["language", "title", "description", "blocker_note_markdown"],
            "additionalProperties": False,
        }
        prompt = self._prompt(
            kind=kind,
            manifest=manifest,
            execution_report=execution_report,
            language_hint=language_hint,
        )
        return PRSummary.model_validate(
            self._run_structured(
                repo_root=repo_root,
                worktree_path=worktree_path,
                prompt=prompt,
                schema=schema,
                session_id=session_id,
            )
        )

    def _run_structured(
        self,
        *,
        repo_root: str | Path,
        worktree_path: str | Path,
        prompt: str,
        schema: dict,
        session_id: str | None,
    ) -> dict:
        with tempfile.TemporaryDirectory(prefix="damon-summary-") as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "schema.json"
            output_path = temp_path / "out.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")

            command = self._base_command(
                repo_root=repo_root,
                worktree_path=worktree_path,
                session_id=session_id,
            )
            command.extend(
                [
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(output_path),
                    prompt,
                ]
            )
            try:
                completed = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Codex PR summarizer timed out.\n{_coerce_stream(exc.stderr)}".strip()) from exc

            if completed.returncode != 0:
                raise RuntimeError(
                    "Codex PR summarizer failed.\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            self.session_id = self._extract_session_id(completed.stderr) or self.session_id or session_id
            if not output_path.exists():
                raise RuntimeError("Codex PR summarizer did not produce structured output.")
            return json.loads(output_path.read_text(encoding="utf-8"))

    def _base_command(
        self,
        *,
        repo_root: str | Path,
        worktree_path: str | Path,
        session_id: str | None,
    ) -> list[str]:
        if session_id:
            return [
                "codex",
                "exec",
                "resume",
                session_id,
                "-c",
                f'model_reasoning_effort="{self.reasoning_effort}"',
            ]
        return [
            "codex",
            "exec",
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            "--cd",
            str(Path(worktree_path).resolve()),
            "--sandbox",
            self.sandbox_mode,
        ]

    def _prompt(
        self,
        *,
        kind: str,
        manifest: dict,
        execution_report: dict,
        language_hint: str,
    ) -> str:
        language_name = "Chinese" if language_hint == "zh" else "English"
        return f"""You are Damon, the delivery summarizer for an autonomous software engineering workflow.

Work in {language_name}. All natural-language fields must use {language_name}.
Read the current repository/worktree state if needed, but focus on the execution report and the run manifest.

Run manifest:
{json.dumps(manifest, ensure_ascii=False, indent=2)}

Execution report:
{json.dumps(execution_report, ensure_ascii=False, indent=2)}

You are preparing a {kind} merge request.

Rules:
- title should be concise and human-readable.
- description should be a polished merge request body, not a raw dump.
- For complete PRs, explain what changed, how it was validated, and any residual risks.
- For blocked PRs, explain what was completed, what remains blocked, what was tried, and what decision or fix is needed next.
- blocker_note_markdown should be empty for complete PRs.
- blocker_note_markdown should be a concise markdown note for blocked PRs that can be committed into the branch.
"""

    def _extract_session_id(self, stderr: str) -> str | None:
        match = re.search(r"session id:\s*([0-9a-f-]+)", stderr, re.IGNORECASE)
        if match:
            return match.group(1)
        return None


def _coerce_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
