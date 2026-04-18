from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class CommandSpec(BaseModel):
    name: str
    command: str
    required: bool = True
    timeout_seconds: int | None = None

    @model_validator(mode="after")
    def validate_command(self) -> "CommandSpec":
        if not self.name.strip():
            raise ValueError("command name must not be empty")
        if not self.command.strip():
            raise ValueError("command must not be empty")
        if self.timeout_seconds is not None and self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be at least 1 when provided")
        return self


class RepositoryProfile(BaseModel):
    version: str
    worktree_root: str = ".damon/worktrees"
    setup_commands: list[CommandSpec] = Field(default_factory=list)
    lint_commands: list[CommandSpec] = Field(default_factory=list)
    test_commands: list[CommandSpec] = Field(default_factory=list)
    static_analysis_commands: list[CommandSpec] = Field(default_factory=list)
    run_review: bool = False
    commit_changes: bool = True
    commit_message_template: str = "{task_id}: {title}"

    @model_validator(mode="after")
    def validate_profile(self) -> "RepositoryProfile":
        if "{task_id}" not in self.commit_message_template:
            raise ValueError("commit_message_template must include {task_id}")
        if "{title}" not in self.commit_message_template:
            raise ValueError("commit_message_template must include {title}")
        return self
