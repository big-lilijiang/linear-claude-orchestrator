import unittest

from damon_autocoding.models import (
    CommitStrategy,
    Complexity,
    DeliveryPolicy,
    EscalationPolicy,
    ExecutionPolicy,
    ExecutionSettings,
    GitLabPolicy,
    GitPolicy,
    PathConstraints,
    PlanningPolicy,
    RepositoryContext,
    RunAction,
    Stage,
    TaskDeliverables,
    TaskHandoff,
    TaskInputs,
    TaskPlan,
    TaskRuntimeState,
    VerificationPolicy,
    WorkerTask,
)
from damon_autocoding.orchestrator import ControlPlane


def make_policy() -> ExecutionPolicy:
    return ExecutionPolicy(
        version="0.1",
        planning=PlanningPolicy(),
        escalation=EscalationPolicy(
            blocker_categories=[
                "missing_credentials",
                "destructive_operation_outside_policy",
            ],
            retry_budget_per_stage=3,
            consecutive_failure_limit=2,
        ),
        delivery=DeliveryPolicy(
            git=GitPolicy(branch_prefix="damon/", commit_strategy=CommitStrategy.CHECKPOINT, push_after_green=True),
            gitlab=GitLabPolicy(open_merge_request=True, draft_by_default=True, labels=["damon"]),
        ),
        verification=VerificationPolicy(
            require_unit_tests=True,
            require_lint=True,
            require_static_analysis=False,
            require_ci_green=True,
            reviewer_agent_required=True,
        ),
        execution=ExecutionSettings(),
    )


def make_task() -> WorkerTask:
    return WorkerTask(
        version="0.1",
        task_id="TASK-1",
        title="Bootstrap system",
        objective="Create a control plane prototype.",
        repository=RepositoryContext(
            path=".",
            default_branch="main",
            working_branch="damon/task-1",
            target_branch="main",
        ),
        acceptance_criteria=["Control plane can simulate next-step decisions."],
        constraints=PathConstraints(allowed_paths=["src/**"], forbidden_paths=["infra/**"], max_changed_files=10),
        inputs=TaskInputs(architecture_refs=["docs/architecture.md"], policy_ref="configs/execution_policy.yaml"),
        plan=TaskPlan(parent_goal="Bootstrap the system.", estimated_complexity=Complexity.SMALL),
        deliverables=TaskDeliverables(expected_outputs=["patch"]),
        handoff=TaskHandoff(must_produce=["implementation_notes"]),
    )


class ControlPlaneTests(unittest.TestCase):
    def test_planning_moves_to_analysis(self) -> None:
        decision = ControlPlane(make_policy()).decide(make_task(), TaskRuntimeState(stage=Stage.PLANNING))
        self.assertEqual(decision.action, RunAction.CONTINUE)
        self.assertEqual(decision.next_stage, Stage.ANALYSIS)

    def test_excess_failures_escalate(self) -> None:
        state = TaskRuntimeState(stage=Stage.VERIFY, consecutive_failures=2)
        decision = ControlPlane(make_policy()).decide(make_task(), state)
        self.assertEqual(decision.action, RunAction.ESCALATE_TO_HUMAN)

    def test_green_verification_moves_to_delivery(self) -> None:
        state = TaskRuntimeState(
            stage=Stage.VERIFY,
            tests_passed=True,
            lint_passed=True,
            ci_green=True,
        )
        decision = ControlPlane(make_policy()).decide(make_task(), state)
        self.assertEqual(decision.action, RunAction.CONTINUE)
        self.assertEqual(decision.next_stage, Stage.DELIVER)


if __name__ == "__main__":
    unittest.main()
