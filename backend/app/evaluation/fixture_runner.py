"""Deterministic executor used by the local no-credentials Eval lane."""

from __future__ import annotations

from app.agent.domain import ToolResult, ToolStatus
from app.evaluation.harness import EvalRunner, ExecutionRequest, ExecutionResult


class FixtureToolAdapter:
    """Eval-only adapter that records required capabilities without real I/O."""

    tool_schemas: dict[str, dict] = {}

    def execute(self, _tool_name: str, _arguments: dict) -> ToolResult:
        return ToolResult(status=ToolStatus.OK)


class FixtureExecutor:
    def __init__(self, requested_mode: str) -> None:
        self._requested_mode = requested_mode

    def run(self, request: ExecutionRequest) -> ExecutionResult:
        outcome_key = (
            f"{self._requested_mode}_resume"
            if request.task_id is not None
            else self._requested_mode
        )
        outcomes = request.fixture.get("deterministic_outcomes", {})
        outcome = outcomes.get(outcome_key)
        if not isinstance(outcome, dict):
            raise ValueError(f"fixture is missing deterministic outcome for {outcome_key}")
        if request.tool_adapter is not None and not outcome.get("needs_user_input", False):
            for capability in request.case.required_capabilities:
                request.tool_adapter.execute(capability, {})
        execution_mode = outcome.get("execution_mode")
        if execution_mode is None:
            execution_mode = "agent" if self._requested_mode == "agent" else "workflow"
        needs_user_input = outcome.get("needs_user_input", False)
        return ExecutionResult(
            execution_mode=execution_mode,
            answer=outcome.get("answer", ""),
            status=outcome.get("status", "FAILED"),
            task_id=outcome.get("task_id") or (
                f"fixture:{self._requested_mode}:{request.case.case_id}"
                if needs_user_input
                else request.task_id
            ),
            trace_ref=outcome.get("trace_ref"),
            failure_category=outcome.get("failure_category"),
            needs_user_input=needs_user_input,
            requested_fields=outcome.get("requested_fields", []),
            verification_executed=outcome.get("verification_executed", True),
            supported=outcome.get("supported", True),
        )


def build_deterministic_runner() -> EvalRunner:
    return EvalRunner(
        workflow_executor=FixtureExecutor("workflow"),
        agent_executor=FixtureExecutor("agent"),
        auto_executor=FixtureExecutor("auto"),
        tool_adapter_factory=lambda _case, _fixture: FixtureToolAdapter(),
    )
