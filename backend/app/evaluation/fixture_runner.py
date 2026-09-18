"""Deterministic executor used by the local no-credentials Eval lane."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent.domain import ToolResult, ToolStatus
from app.evaluation.harness import EvalRunner, ExecutionRequest, ExecutionResult


class FixtureToolAdapter:
    """Eval-only adapter that records required capabilities without real I/O."""

    tool_schemas: dict[str, dict] = {}

    def __init__(self, tool_script: list[dict[str, Any]] | None = None) -> None:
        self._tool_script = tool_script or []
        self._next_script_step = 0

    def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        if self._next_script_step >= len(self._tool_script):
            return ToolResult(status=ToolStatus.OK)
        step = self._tool_script[self._next_script_step]
        if step.get("tool_name") != tool_name:
            raise ValueError(
                f"fixture expected {step.get('tool_name')}, got {tool_name}"
            )
        if step.get("arguments", {}) != arguments:
            raise ValueError(
                f"fixture arguments for {tool_name} did not match the tool script"
            )
        self._next_script_step += 1
        return ToolResult.model_validate(step.get("result", {"status": "OK"}))


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
        default_key = "default_resume" if request.task_id is not None else "default"
        default_outcome = outcomes.get(default_key, {})
        specific_outcome = outcomes.get(outcome_key, {})
        if not isinstance(default_outcome, dict) or not isinstance(specific_outcome, dict):
            raise ValueError(f"fixture is missing deterministic outcome for {outcome_key}")
        outcome = {**default_outcome, **specific_outcome}
        if not outcome:
            raise ValueError(f"fixture is missing deterministic outcome for {outcome_key}")
        if request.tool_adapter is not None and not outcome.get("needs_user_input", False):
            self._run_tool_script(request)
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
            verification_rejection_count=outcome.get("verification_rejection_count", 0),
            supported=outcome.get("supported", True),
        )

    @staticmethod
    def _run_tool_script(request: ExecutionRequest) -> None:
        script = request.fixture.get("tool_script")
        if not isinstance(script, list):
            for capability in request.case.required_capabilities:
                request.tool_adapter.execute(capability, {})
            return

        previous_result: ToolResult | None = None
        for raw_step in script:
            if not isinstance(raw_step, dict):
                raise ValueError("tool_script steps must be objects")
            tool_name = raw_step.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name:
                raise ValueError("tool_script step requires tool_name")
            arguments = deepcopy(raw_step.get("arguments", {}))
            if not isinstance(arguments, dict):
                raise ValueError("tool_script arguments must be an object")
            from_previous = raw_step.get("arguments_from_previous", {})
            if not isinstance(from_previous, dict):
                raise ValueError("arguments_from_previous must be an object")
            if from_previous and previous_result is None:
                raise ValueError("arguments_from_previous requires an earlier tool result")
            for argument_name, result_key in from_previous.items():
                if not isinstance(argument_name, str) or not isinstance(result_key, str):
                    raise ValueError("observation argument mappings must be strings")
                assert previous_result is not None
                if result_key not in previous_result.data:
                    raise ValueError(
                        f"previous tool result has no {result_key} for {argument_name}"
                    )
                arguments[argument_name] = previous_result.data[result_key]
            raw_step["arguments"] = deepcopy(arguments)
            result = request.tool_adapter.execute(tool_name, arguments)
            if raw_step.get("retry_on_retryable", False) and result.retryable:
                result = request.tool_adapter.execute(tool_name, arguments)
            previous_result = result


def build_deterministic_runner() -> EvalRunner:
    return EvalRunner(
        workflow_executor=FixtureExecutor("workflow"),
        agent_executor=FixtureExecutor("agent"),
        auto_executor=FixtureExecutor("auto"),
        tool_adapter_factory=lambda _case, fixture: FixtureToolAdapter(
            fixture.get("tool_script")
        ),
    )
