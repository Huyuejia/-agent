"""Deterministic, version-controlled evaluation harness for execution routing."""

from __future__ import annotations

from copy import deepcopy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.agent.domain import FailureCategory, TaskStatus, ToolResult, ToolStatus


class FaultInjection(BaseModel):
    """A test-only fault that affects the first matching tool call."""

    kind: Literal["retryable_error_first", "not_found_first"]
    tool_name: str | None = None


class EvalCase(BaseModel):
    case_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    slice: str = Field(min_length=1)
    complex_task: bool
    initial_message: str = Field(min_length=1)
    expected_boundary: Literal["workflow", "agent"]
    required_capabilities: list[str] = Field(default_factory=list)
    forbidden_capabilities: list[str] = Field(default_factory=list)
    required_facts: list[str] = Field(default_factory=list)
    expected_status: TaskStatus = TaskStatus.SUCCEEDED
    expected_requested_fields: list[str] = Field(default_factory=list)
    scripted_user_inputs: dict[str, str] = Field(default_factory=dict)
    fault_injection: FaultInjection | None = None
    max_tool_calls: int = Field(default=6, gt=0)
    max_tool_retries: int = Field(default=1, ge=0)
    fixture: dict[str, Any] = Field(default_factory=dict)
    requires_verification: bool = True


class EvalEvent(BaseModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None


class ToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult


class ExecutionRequest:
    """Public seam passed to a workflow, Agent, or auto-mode executor."""

    def __init__(
        self,
        *,
        case: EvalCase,
        requested_mode: Literal["workflow", "agent", "auto"],
        fixture: dict[str, Any],
        tool_adapter: ToolAdapter | None = None,
        task_id: str | None = None,
        scripted_user_input: dict[str, str] | None = None,
    ) -> None:
        self.case = case
        self.requested_mode = requested_mode
        self.fixture = fixture
        self.tool_adapter = tool_adapter
        self.task_id = task_id
        self.scripted_user_input = scripted_user_input


class ExecutionResult(BaseModel):
    execution_mode: Literal["workflow", "agent"]
    answer: str = ""
    status: TaskStatus
    task_id: str | None = None
    trace_ref: str | None = None
    failure_category: FailureCategory | None = None
    needs_user_input: bool = False
    requested_fields: list[str] = Field(default_factory=list)
    verification_executed: bool = False
    supported: bool = True
    events: list[EvalEvent] = Field(default_factory=list)


class EvalExecutor(Protocol):
    def run(self, request: ExecutionRequest) -> ExecutionResult: ...


class ToolAdapter(Protocol):
    tool_schemas: dict[str, dict[str, Any]]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult: ...


class GraderDetail(BaseModel):
    passed: bool
    message: str


class EvalResult(BaseModel):
    case_id: str
    case_version: str
    requested_mode: Literal["workflow", "agent", "auto"]
    execution_mode: Literal["workflow", "agent"]
    grader_details: dict[str, GraderDetail]
    success: bool
    trace_refs: list[str] = Field(default_factory=list)
    failure_category: FailureCategory | None = None
    events: list[EvalEvent] = Field(default_factory=list)


class FaultInjectingToolAdapter:
    """Wrap a normal adapter without changing its production behaviour."""

    def __init__(self, adapter: ToolAdapter, fault: FaultInjection | None) -> None:
        self._adapter = adapter
        self._fault = fault
        self._injected = False
        self.tool_calls: list[ToolCall] = []

    @property
    def tool_schemas(self) -> dict[str, dict[str, Any]]:
        return self._adapter.tool_schemas

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if self._should_inject(tool_name):
            self._injected = True
            result = self._injected_result()
        else:
            result = self._adapter.execute(tool_name, arguments)
        self.tool_calls.append(
            ToolCall(tool_name=tool_name, arguments=arguments, result=result)
        )
        return result

    def _should_inject(self, tool_name: str) -> bool:
        return bool(
            self._fault
            and not self._injected
            and (self._fault.tool_name is None or self._fault.tool_name == tool_name)
        )

    def _injected_result(self) -> ToolResult:
        assert self._fault is not None
        if self._fault.kind == "not_found_first":
            return ToolResult(status=ToolStatus.NOT_FOUND)
        return ToolResult(
            status=ToolStatus.ERROR,
            error_code="EVAL_INJECTED_RETRYABLE_ERROR",
            retryable=True,
        )


class EvalRunner:
    """Run every EvalCase through forced and auto execution without model I/O."""

    def __init__(
        self,
        *,
        workflow_executor: EvalExecutor,
        agent_executor: EvalExecutor,
        auto_executor: EvalExecutor,
        tool_adapter_factory: Callable[[EvalCase, dict[str, Any]], ToolAdapter] | None = None,
    ) -> None:
        self._executors = {
            "workflow": workflow_executor,
            "agent": agent_executor,
            "auto": auto_executor,
        }
        self._tool_adapter_factory = tool_adapter_factory

    def run_cases(self, cases: list[EvalCase]) -> list[EvalResult]:
        return [result for case in cases for result in self.run_case(case)]

    def run_case(self, case: EvalCase) -> list[EvalResult]:
        return [self._run_mode(case, mode) for mode in ("workflow", "agent", "auto")]

    def _run_mode(
        self, case: EvalCase, mode: Literal["workflow", "agent", "auto"]
    ) -> EvalResult:
        fixture = deepcopy(case.fixture)
        adapter = self._new_tool_adapter(case, fixture)
        executor = self._executors[mode]
        request = ExecutionRequest(
            case=case,
            requested_mode=mode,
            fixture=fixture,
            tool_adapter=adapter,
        )
        executions = [executor.run(request)]
        first = executions[0]
        if first.needs_user_input and self._has_scripted_input(case, first):
            executions.append(
                executor.run(
                    ExecutionRequest(
                        case=case,
                        requested_mode=mode,
                        fixture=fixture,
                        tool_adapter=adapter,
                        task_id=first.task_id,
                        scripted_user_input={
                            field: case.scripted_user_inputs[field]
                            for field in first.requested_fields
                        },
                    )
                )
            )
        final = executions[-1]
        calls = adapter.tool_calls if adapter is not None else []
        details = self._grade(case, mode, executions, calls)
        success = all(detail.passed for detail in details.values())
        events = [event for execution in executions for event in execution.events]
        events.append(
            EvalEvent(
                event_type="EVAL_RESULT",
                payload={
                    "case_id": case.case_id,
                    "case_version": case.version,
                    "requested_mode": mode,
                    "execution_mode": final.execution_mode,
                    "success": success,
                    "failure_category": final.failure_category,
                },
            )
        )
        return EvalResult(
            case_id=case.case_id,
            case_version=case.version,
            requested_mode=mode,
            execution_mode=final.execution_mode,
            grader_details=details,
            success=success,
            trace_refs=[
                execution.trace_ref for execution in executions if execution.trace_ref
            ],
            failure_category=final.failure_category,
            events=events,
        )

    def _new_tool_adapter(
        self, case: EvalCase, fixture: dict[str, Any]
    ) -> FaultInjectingToolAdapter | None:
        if self._tool_adapter_factory is None:
            return None
        return FaultInjectingToolAdapter(
            self._tool_adapter_factory(case, fixture), case.fault_injection
        )

    @staticmethod
    def _has_scripted_input(case: EvalCase, result: ExecutionResult) -> bool:
        return bool(result.task_id) and all(
            field in case.scripted_user_inputs for field in result.requested_fields
        )

    def _grade(
        self,
        case: EvalCase,
        requested_mode: Literal["workflow", "agent", "auto"],
        executions: list[ExecutionResult],
        calls: list[ToolCall],
    ) -> dict[str, GraderDetail]:
        first, final = executions[0], executions[-1]
        answer = final.answer.casefold()
        missing_facts = [fact for fact in case.required_facts if fact.casefold() not in answer]
        observed_capabilities = {call.tool_name for call in calls}
        missing_capabilities = sorted(set(case.required_capabilities) - observed_capabilities)
        forbidden_capabilities = sorted(
            set(case.forbidden_capabilities) & observed_capabilities
        )
        retries = self._retry_count(calls)
        clarification_ok = (
            not case.expected_requested_fields
            or (
                first.needs_user_input
                and first.requested_fields == case.expected_requested_fields
                and (len(executions) > 1 or final.status is TaskStatus.WAITING_FOR_USER)
            )
        )
        details = {
            "outcome_facts": GraderDetail(
                passed=not missing_facts,
                message="required facts present" if not missing_facts else f"missing facts: {missing_facts}",
            ),
            "outcome_status": GraderDetail(
                passed=final.status == case.expected_status,
                message=f"expected {case.expected_status.value}, got {final.status.value}",
            ),
            "outcome_clarification": GraderDetail(
                passed=clarification_ok,
                message="clarification behaviour matched" if clarification_ok else "requested fields did not match",
            ),
            "outcome_supported_success": GraderDetail(
                passed=not (final.status is TaskStatus.SUCCEEDED and not final.supported),
                message="success is evidence-supported" if final.supported else "unsupported success",
            ),
            "process_capabilities": GraderDetail(
                passed=not missing_capabilities and not forbidden_capabilities,
                message=(
                    "capabilities matched"
                    if not missing_capabilities and not forbidden_capabilities
                    else f"missing={missing_capabilities}, forbidden={forbidden_capabilities}"
                ),
            ),
            "process_limits": GraderDetail(
                passed=len(calls) <= case.max_tool_calls and retries <= case.max_tool_retries,
                message=f"tool_calls={len(calls)}, retries={retries}",
            ),
            "process_verification": GraderDetail(
                passed=not case.requires_verification or final.verification_executed,
                message="verification executed" if final.verification_executed else "verification missing",
            ),
        }
        expected_mode = (
            case.expected_boundary if requested_mode == "auto" else requested_mode
        )
        details["boundary"] = GraderDetail(
            passed=final.execution_mode == expected_mode,
            message=f"expected {expected_mode}, got {final.execution_mode}",
        )
        return details

    @staticmethod
    def _retry_count(calls: list[ToolCall]) -> int:
        retries = 0
        previous: ToolCall | None = None
        for call in calls:
            if (
                previous is not None
                and previous.tool_name == call.tool_name
                and previous.arguments == call.arguments
                and previous.result.status is ToolStatus.ERROR
                and previous.result.retryable
            ):
                retries += 1
            previous = call
        return retries


def load_cases(path: Path | str) -> list[EvalCase]:
    """Load non-empty JSONL rows so EvalCase changes remain reviewable in Git."""

    cases: list[EvalCase] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(EvalCase.model_validate_json(line))
        except ValueError as error:
            raise ValueError(f"invalid EvalCase at line {line_number}") from error
    if not cases:
        raise ValueError("EvalCase file must contain at least one case")
    return cases
