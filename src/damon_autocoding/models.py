from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class AutonomyMode(StrEnum):
    PLAN_THEN_EXECUTE = "plan_then_execute"


class CommitStrategy(StrEnum):
    CHECKPOINT = "checkpoint"
    SQUASH_AT_END = "squash_at_end"


class WorkspaceStrategy(StrEnum):
    GIT_WORKTREE = "git_worktree"
    CLONE = "clone"


class Complexity(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class Stage(StrEnum):
    PLANNING = "planning"
    ANALYSIS = "analysis"
    IMPLEMENT = "implement"
    REVIEW = "review"
    VERIFY = "verify"
    DELIVER = "deliver"
    COMPLETE = "complete"


class RunAction(StrEnum):
    CONTINUE = "continue"
    RETRY_STAGE = "retry_stage"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    OPEN_MERGE_REQUEST = "open_merge_request"
    COMPLETE = "complete"


class PlanningPolicy(BaseModel):
    require_architecture: bool = True
    require_constraints: bool = True
    require_definition_of_done: bool = True
    require_execution_policy: bool = True


class EscalationPolicy(BaseModel):
    blocker_categories: list[str] = Field(default_factory=list)
    retry_budget_per_stage: int = 3
    consecutive_failure_limit: int = 2
    max_unattended_cycles: int = 12


class GitPolicy(BaseModel):
    branch_prefix: str = "damon/"
    commit_strategy: CommitStrategy = CommitStrategy.CHECKPOINT
    push_after_green: bool = True


class GitLabPolicy(BaseModel):
    open_merge_request: bool = True
    draft_by_default: bool = False
    labels: list[str] = Field(default_factory=lambda: ["damon"])


class DeliveryPolicy(BaseModel):
    git: GitPolicy = Field(default_factory=GitPolicy)
    gitlab: GitLabPolicy = Field(default_factory=GitLabPolicy)


class VerificationPolicy(BaseModel):
    require_unit_tests: bool = True
    require_lint: bool = True
    require_static_analysis: bool = False
    require_ci_green: bool = True
    reviewer_agent_required: bool = True


class CodexExecutionPolicy(BaseModel):
    approval_mode: str = "never"
    sandbox_mode: str = "workspace-write"


class ExecutionSettings(BaseModel):
    allow_parallel_workers: bool = True
    max_parallel_workers: int = 3
    isolated_workspace: WorkspaceStrategy = WorkspaceStrategy.GIT_WORKTREE
    default_timeout_minutes: int = 45
    codex: CodexExecutionPolicy = Field(default_factory=CodexExecutionPolicy)

    @model_validator(mode="after")
    def validate_parallelism(self) -> "ExecutionSettings":
        if not self.allow_parallel_workers and self.max_parallel_workers != 1:
            self.max_parallel_workers = 1
        if self.max_parallel_workers < 1:
            raise ValueError("max_parallel_workers must be at least 1")
        return self


class ExecutionPolicy(BaseModel):
    version: str
    autonomy_mode: AutonomyMode = AutonomyMode.PLAN_THEN_EXECUTE
    allow_silent_replans: bool = True
    planning: PlanningPolicy = Field(default_factory=PlanningPolicy)
    escalation: EscalationPolicy = Field(default_factory=EscalationPolicy)
    delivery: DeliveryPolicy = Field(default_factory=DeliveryPolicy)
    verification: VerificationPolicy = Field(default_factory=VerificationPolicy)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)

    def should_escalate(
        self,
        *,
        reason: str | None,
        stage_retry_count: int,
        consecutive_failures: int,
        destructive_operation_requested: bool,
    ) -> bool:
        if destructive_operation_requested:
            return "destructive_operation_outside_policy" in self.escalation.blocker_categories
        if reason and reason in self.escalation.blocker_categories:
            return True
        if stage_retry_count > self.escalation.retry_budget_per_stage:
            return True
        if consecutive_failures >= self.escalation.consecutive_failure_limit:
            return True
        return False


class RepositoryContext(BaseModel):
    path: Path
    default_branch: str = "main"
    base_ref: str | None = None
    working_branch: str
    target_branch: str = "main"

    @model_validator(mode="after")
    def validate_branches(self) -> "RepositoryContext":
        if self.working_branch == self.target_branch:
            raise ValueError("working_branch must differ from target_branch")
        if self.base_ref is not None and not self.base_ref.strip():
            raise ValueError("base_ref must not be empty when provided")
        return self


class PathConstraints(BaseModel):
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    max_changed_files: int = 20

    @model_validator(mode="after")
    def validate_limits(self) -> "PathConstraints":
        if self.max_changed_files < 1:
            raise ValueError("max_changed_files must be at least 1")
        return self


class TaskInputs(BaseModel):
    architecture_refs: list[str] = Field(default_factory=list)
    policy_ref: str
    related_issues: list[str] = Field(default_factory=list)


class TaskPlan(BaseModel):
    parent_goal: str
    depends_on: list[str] = Field(default_factory=list)
    estimated_complexity: Complexity = Complexity.SMALL
    owning_agent: str = "implementer"
    worker_type: str = "codex"


class TaskDeliverables(BaseModel):
    code_changes_required: bool = True
    tests_required: bool = True
    docs_required: bool = False
    expected_outputs: list[str] = Field(default_factory=list)


class TaskHandoff(BaseModel):
    must_produce: list[str] = Field(default_factory=list)
    reviewer: str = "reviewer"
    merge_request_template: str | None = None


class TaskEscalationRules(BaseModel):
    stop_if: list[str] = Field(default_factory=list)
    notify_if: list[str] = Field(default_factory=list)


class WorkerTask(BaseModel):
    version: str
    task_id: str
    title: str
    objective: str
    repository: RepositoryContext
    acceptance_criteria: list[str]
    constraints: PathConstraints
    inputs: TaskInputs
    plan: TaskPlan
    deliverables: TaskDeliverables
    handoff: TaskHandoff
    escalation_rules: TaskEscalationRules = Field(default_factory=TaskEscalationRules)

    @model_validator(mode="after")
    def validate_task(self) -> "WorkerTask":
        if not self.acceptance_criteria:
            raise ValueError("acceptance_criteria must not be empty")
        if not self.inputs.architecture_refs:
            raise ValueError("at least one architecture reference is required")
        return self


class TaskRuntimeState(BaseModel):
    stage: Stage = Stage.PLANNING
    stage_retry_count: int = 0
    consecutive_failures: int = 0
    implementation_complete: bool = False
    review_passed: bool = False
    tests_passed: bool = False
    lint_passed: bool = False
    static_analysis_passed: bool = False
    ci_green: bool = False
    merge_request_opened: bool = False
    destructive_operation_requested: bool = False
    blocker_reason: str | None = None
    notes: str | None = None


class RunDecision(BaseModel):
    action: RunAction
    next_stage: Stage | None = None
    reason: str
