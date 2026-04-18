from __future__ import annotations

from .models import ExecutionPolicy, RunAction, RunDecision, Stage, TaskRuntimeState, WorkerTask


class ControlPlane:
    def __init__(self, policy: ExecutionPolicy) -> None:
        self.policy = policy

    def decide(self, task: WorkerTask, state: TaskRuntimeState) -> RunDecision:
        if self.policy.should_escalate(
            reason=state.blocker_reason,
            stage_retry_count=state.stage_retry_count,
            consecutive_failures=state.consecutive_failures,
            destructive_operation_requested=state.destructive_operation_requested,
        ):
            return RunDecision(
                action=RunAction.ESCALATE_TO_HUMAN,
                next_stage=state.stage,
                reason="Runtime state exceeded autonomous execution policy.",
            )

        if state.stage == Stage.PLANNING:
            return RunDecision(
                action=RunAction.CONTINUE,
                next_stage=Stage.ANALYSIS,
                reason="Planning inputs are present, repository analysis can start.",
            )

        if state.stage == Stage.ANALYSIS:
            return RunDecision(
                action=RunAction.CONTINUE,
                next_stage=Stage.IMPLEMENT,
                reason=f"Analysis is complete enough to assign implementation for {task.task_id}.",
            )

        if state.stage == Stage.IMPLEMENT:
            if not state.implementation_complete:
                action = RunAction.RETRY_STAGE if state.stage_retry_count > 0 else RunAction.CONTINUE
                return RunDecision(
                    action=action,
                    next_stage=Stage.IMPLEMENT,
                    reason="Implementation is still in progress and should continue without escalation.",
                )
            return RunDecision(
                action=RunAction.CONTINUE,
                next_stage=Stage.REVIEW,
                reason="Implementation is complete, send the diff to reviewer.",
            )

        if state.stage == Stage.REVIEW:
            if self.policy.verification.reviewer_agent_required and not state.review_passed:
                return RunDecision(
                    action=RunAction.CONTINUE,
                    next_stage=Stage.REVIEW,
                    reason="Reviewer approval is required before verification can proceed.",
                )
            return RunDecision(
                action=RunAction.CONTINUE,
                next_stage=Stage.VERIFY,
                reason="Review passed, move to verification.",
            )

        if state.stage == Stage.VERIFY:
            if not self._verification_complete(state):
                action = RunAction.RETRY_STAGE if state.stage_retry_count > 0 else RunAction.CONTINUE
                return RunDecision(
                    action=action,
                    next_stage=Stage.VERIFY,
                    reason="Verification gates are not all green yet.",
                )
            return RunDecision(
                action=RunAction.CONTINUE,
                next_stage=Stage.DELIVER,
                reason="Required verification checks are green, delivery can start.",
            )

        if state.stage == Stage.DELIVER:
            if self.policy.delivery.gitlab.open_merge_request and not state.merge_request_opened:
                return RunDecision(
                    action=RunAction.OPEN_MERGE_REQUEST,
                    next_stage=Stage.DELIVER,
                    reason="Delivery policy requires a GitLab merge request.",
                )
            return RunDecision(
                action=RunAction.COMPLETE,
                next_stage=Stage.COMPLETE,
                reason="Delivery artifacts are complete.",
            )

        return RunDecision(
            action=RunAction.COMPLETE,
            next_stage=Stage.COMPLETE,
            reason="Task is already complete.",
        )

    def _verification_complete(self, state: TaskRuntimeState) -> bool:
        gates = []
        if self.policy.verification.require_unit_tests:
            gates.append(state.tests_passed)
        if self.policy.verification.require_lint:
            gates.append(state.lint_passed)
        if self.policy.verification.require_static_analysis:
            gates.append(state.static_analysis_passed)
        if self.policy.verification.require_ci_green:
            gates.append(state.ci_green)
        return all(gates)
