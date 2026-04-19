from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class PlanningMessage(BaseModel):
    role: str
    content: str


class FeatureCandidate(BaseModel):
    title: str
    rationale: str
    impact: str
    effort: str


class PlannerTurn(BaseModel):
    language: str = "en"
    repo_summary: str
    repo_risks: list[str] = Field(default_factory=list)
    candidate_features: list[FeatureCandidate] = Field(default_factory=list)
    recommendation: str
    questions: list[str] = Field(default_factory=list)
    ready_for_dossier: bool = False
    reply_to_user: str


class DossierDraft(BaseModel):
    language: str = "en"
    title: str
    goal: str
    scope_items: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    architecture_notes: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    lint_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    static_analysis_commands: list[str] = Field(default_factory=list)
    target_branch: str = "main"
    base_ref: str | None = None
    working_branch: str
    run_review: bool = False
    auto_push_complete_pr: bool = True
    auto_push_blocked_pr: bool = True
    draft_merge_request: bool = False
    summary_for_user: str
    goal_markdown: str
    architecture_markdown: str
    repo_scan_markdown: str


class PlannerBackend(Protocol):
    def next_turn(
        self,
        *,
        repo_root: str | Path,
        goal: str,
        scan_summary: dict,
        transcript: list[PlanningMessage],
        language_hint: str,
    ) -> PlannerTurn:
        ...

    def build_dossier(
        self,
        *,
        repo_root: str | Path,
        goal: str,
        scan_summary: dict,
        transcript: list[PlanningMessage],
        language_hint: str,
    ) -> DossierDraft:
        ...


class CodexPlannerBackend:
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

    def next_turn(
        self,
        *,
        repo_root: str | Path,
        goal: str,
        scan_summary: dict,
        transcript: list[PlanningMessage],
        language_hint: str,
    ) -> PlannerTurn:
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["zh", "en"]},
                "repo_summary": {"type": "string"},
                "repo_risks": {"type": "array", "items": {"type": "string"}},
                "candidate_features": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "rationale": {"type": "string"},
                            "impact": {"type": "string"},
                            "effort": {"type": "string"},
                        },
                        "required": ["title", "rationale", "impact", "effort"],
                        "additionalProperties": False,
                    },
                },
                "recommendation": {"type": "string"},
                "questions": {"type": "array", "items": {"type": "string"}},
                "ready_for_dossier": {"type": "boolean"},
                "reply_to_user": {"type": "string"},
            },
            "required": [
                "language",
                "repo_summary",
                "repo_risks",
                "candidate_features",
                "recommendation",
                "questions",
                "ready_for_dossier",
                "reply_to_user",
            ],
            "additionalProperties": False,
        }
        prompt = self._turn_prompt(
            goal=goal,
            scan_summary=scan_summary,
            transcript=transcript,
            language_hint=language_hint,
        )
        return PlannerTurn.model_validate(self._run_structured(repo_root=repo_root, prompt=prompt, schema=schema))

    def build_dossier(
        self,
        *,
        repo_root: str | Path,
        goal: str,
        scan_summary: dict,
        transcript: list[PlanningMessage],
        language_hint: str,
    ) -> DossierDraft:
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["zh", "en"]},
                "title": {"type": "string"},
                "goal": {"type": "string"},
                "scope_items": {"type": "array", "items": {"type": "string"}},
                "non_goals": {"type": "array", "items": {"type": "string"}},
                "architecture_notes": {"type": "array", "items": {"type": "string"}},
                "allowed_paths": {"type": "array", "items": {"type": "string"}},
                "forbidden_paths": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "definition_of_done": {"type": "array", "items": {"type": "string"}},
                "lint_commands": {"type": "array", "items": {"type": "string"}},
                "test_commands": {"type": "array", "items": {"type": "string"}},
                "static_analysis_commands": {"type": "array", "items": {"type": "string"}},
                "target_branch": {"type": "string"},
                "base_ref": {"type": ["string", "null"]},
                "working_branch": {"type": "string"},
                "run_review": {"type": "boolean"},
                "auto_push_complete_pr": {"type": "boolean"},
                "auto_push_blocked_pr": {"type": "boolean"},
                "draft_merge_request": {"type": "boolean"},
                "summary_for_user": {"type": "string"},
                "goal_markdown": {"type": "string"},
                "architecture_markdown": {"type": "string"},
                "repo_scan_markdown": {"type": "string"},
            },
            "required": [
                "language",
                "title",
                "goal",
                "scope_items",
                "non_goals",
                "architecture_notes",
                "allowed_paths",
                "forbidden_paths",
                "constraints",
                "definition_of_done",
                "lint_commands",
                "test_commands",
                "static_analysis_commands",
                "target_branch",
                "base_ref",
                "working_branch",
                "run_review",
                "auto_push_complete_pr",
                "auto_push_blocked_pr",
                "draft_merge_request",
                "summary_for_user",
                "goal_markdown",
                "architecture_markdown",
                "repo_scan_markdown",
            ],
            "additionalProperties": False,
        }
        prompt = self._dossier_prompt(
            goal=goal,
            scan_summary=scan_summary,
            transcript=transcript,
            language_hint=language_hint,
        )
        return DossierDraft.model_validate(self._run_structured(repo_root=repo_root, prompt=prompt, schema=schema))

    def _run_structured(self, *, repo_root: str | Path, prompt: str, schema: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="damon-planner-") as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "schema.json"
            output_path = temp_path / "out.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            resumed = self.session_id is not None
            command = [*self._base_command(repo_root=repo_root), "-o", str(output_path)]
            if resumed:
                command.append(self._json_only_prompt(prompt=prompt, schema=schema))
            else:
                command.extend(["--output-schema", str(schema_path), prompt])
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
                stderr = _coerce_stream(exc.stderr)
                raise RuntimeError(f"Codex planner timed out.\n{stderr}".strip()) from exc

            if completed.returncode != 0:
                raise RuntimeError(
                    "Codex planner failed.\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            self.session_id = self._extract_session_id(completed.stderr) or self.session_id
            if not output_path.exists():
                raise RuntimeError("Codex planner did not produce structured output.")
            return parse_json_output(output_path.read_text(encoding="utf-8"))

    def _base_command(self, *, repo_root: str | Path) -> list[str]:
        if self.session_id:
            return [
                "codex",
                "exec",
                "resume",
                self.session_id,
                "-c",
                f'model_reasoning_effort="{self.reasoning_effort}"',
            ]
        return [
            "codex",
            "exec",
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            "--cd",
            str(Path(repo_root).resolve()),
            "--sandbox",
            self.sandbox_mode,
        ]

    def _json_only_prompt(self, *, prompt: str, schema: dict) -> str:
        return (
            f"{prompt}\n\n"
            "Return ONLY a raw JSON object. Do not wrap it in markdown. "
            "Do not include explanation before or after the JSON.\n"
            f"JSON schema to satisfy exactly:\n{json.dumps(schema, ensure_ascii=False)}"
        )

    def _extract_session_id(self, stderr: str) -> str | None:
        match = re.search(r"session id:\s*([0-9a-f-]+)", stderr, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _turn_prompt(
        self,
        *,
        goal: str,
        scan_summary: dict,
        transcript: list[PlanningMessage],
        language_hint: str,
    ) -> str:
        language_name = "Chinese" if language_hint == "zh" else "English"
        return f"""You are Damon, the planning agent for an autonomous software engineering workflow.

Work in {language_name}. All natural-language fields in your JSON must use {language_name}.

You are in the planning phase, not implementation.
Read the repository to ground your judgment. Use repository evidence and the provided repo scan summary.
Prefer the repo scan summary as your primary context. Only inspect extra files when you need to reduce uncertainty.
Do not run tests, package installs, or long-running commands during planning.
Keep repository exploration lightweight.

Current goal:
{goal}

Repo scan summary:
{json.dumps(scan_summary, ensure_ascii=False, indent=2)}

Conversation transcript:
{json.dumps([message.model_dump(mode="json") for message in transcript], ensure_ascii=False, indent=2)}

Your job in this turn:
1. Summarize the repository and the current planning state.
2. If the goal is vague, propose up to 3 repository-grounded candidate features or tasks worth doing.
3. Recommend the most sensible direction for this repository and goal.
4. Ask only the minimal next questions needed to freeze an execution dossier.
5. If enough information already exists, set ready_for_dossier=true and ask no questions.

Rules:
- Do not ask generic boilerplate questions if the repository or transcript already answers them.
- Questions must be concrete, repository-specific, and useful.
- reply_to_user should read naturally to the human. It should summarize your understanding, mention any feature recommendation, and then ask the next questions.
- Keep the number of questions small, usually 1 to 3.
- If the user goal is \"find a good feature\", choose candidates grounded in the repository instead of asking the user to invent one.
- Use at most a small number of quick repository inspections if you need them.
"""

    def _dossier_prompt(
        self,
        *,
        goal: str,
        scan_summary: dict,
        transcript: list[PlanningMessage],
        language_hint: str,
    ) -> str:
        language_name = "Chinese" if language_hint == "zh" else "English"
        return f"""You are Damon, the dossier writer for an autonomous software engineering workflow.

Work in {language_name}. All natural-language fields must use {language_name}.

Read the repository to ground your decisions. Use the transcript and repository evidence to produce a concrete dossier for execution.
Prefer the repo scan summary as your primary context. Only inspect additional files when needed.
Do not run tests, package installs, or long-running commands during dossier generation.

Current goal:
{goal}

Repo scan summary:
{json.dumps(scan_summary, ensure_ascii=False, indent=2)}

Conversation transcript:
{json.dumps([message.model_dump(mode="json") for message in transcript], ensure_ascii=False, indent=2)}

Produce a practical frozen dossier for execution.

Rules:
- If the original goal was vague, choose the feature or task that best matches the repository and the transcript.
- definition_of_done must be concrete and executable.
- Infer lint/test/static analysis commands from the repository when possible.
- target_branch should usually follow the repository default branch from scan_summary unless the transcript says otherwise.
- base_ref should normally be the current branch or target branch.
- working_branch must be a valid branch name starting with damon/.
- Keep allowed_paths and forbidden_paths empty unless the transcript clearly constrains them.
- goal_markdown, architecture_markdown, and repo_scan_markdown should be polished human-readable markdown files.
- summary_for_user should be a concise planning summary the CLI can show before asking for final freeze confirmation.
"""


def detect_language(text: str) -> str:
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return "zh"
    return "en"


def localized(language: str, key: str) -> str:
    table = {
        "zh": {
            "planning_section": "规划会话",
            "repo_scan_section": "仓库理解",
            "answer_prompt": "请直接回复上面的问题。你可以自由描述取舍、限制、验收标准和优先级。",
            "answer_hint": "直接输入，多行均可；输入空行提交。",
            "initial_goal_prompt": "请先描述你想让 Damon 完成什么目标",
            "initial_goal_hint": "直接描述目标即可，例如：先找一个最值得做的功能，然后完成并提 PR。",
            "freeze_now": "我已经有足够信息生成执行档案。现在生成 dossier 草案并进入最终确认吗？",
            "dossier_summary": "规划总结",
            "freeze_final": "确认冻结这份 dossier，并标记为可执行吗？",
            "empty_answer_retry": "还没有收到有效回复。请至少补充一点方向、限制或完成标准。",
            "reply_label": "你的回复",
            "planning_note": "下面的分析、推荐方向和问题都来自 Codex 对当前仓库的理解。",
            "codex_analyzing": "Codex 正在分析仓库并收敛下一轮问题...",
            "codex_drafting": "Codex 正在生成执行 dossier 草案...",
            "codex_executing": "Codex 正在执行任务，请稍候...",
            "codex_summarizing_complete_pr": "Codex 正在整理完整 PR 摘要...",
            "codex_summarizing_blocked_pr": "Codex 正在整理阻塞 PR 摘要...",
        },
        "en": {
            "planning_section": "Planning Session",
            "repo_scan_section": "Repository Understanding",
            "answer_prompt": "Reply freely to the questions above. You can describe tradeoffs, constraints, acceptance criteria, and priorities.",
            "answer_hint": "Type your answer freely. Submit an empty line to finish.",
            "initial_goal_prompt": "Describe what you want Damon to accomplish",
            "initial_goal_hint": "For example: find the most valuable feature to build, implement it, and open a PR.",
            "freeze_now": "I have enough information to draft the execution dossier. Generate the dossier draft now?",
            "dossier_summary": "Planning Summary",
            "freeze_final": "Freeze this dossier and mark it ready for execution?",
            "empty_answer_retry": "No meaningful reply received yet. Please add at least one constraint, preference, or acceptance detail.",
            "reply_label": "Your reply",
            "planning_note": "The analysis, recommendation, and questions below are generated by Codex from the current repository.",
            "codex_analyzing": "Codex is analyzing the repository and preparing the next planning turn...",
            "codex_drafting": "Codex is drafting the execution dossier...",
            "codex_executing": "Codex is executing the task. Please wait...",
            "codex_summarizing_complete_pr": "Codex is preparing the complete PR summary...",
            "codex_summarizing_blocked_pr": "Codex is preparing the blocked PR summary...",
        },
    }
    return table["zh" if language == "zh" else "en"][key]


def _coerce_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


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
