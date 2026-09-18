"""Controlled-agent domain and routing tests."""

import pytest
from pydantic import ValidationError

from app.agent.domain import (
    AgentCandidate,
    ExecutionMode,
    FailureCategory,
    TaskState,
    TaskStatus,
    ToolResult,
    ToolStatus,
)
from app.agent.attribution import FailureAttributor
from app.agent.routing import ExecutionRouter
from app.agent.verification import CandidateVerifier


def test_router_keeps_deterministic_lookup_on_workflow():
    decision = ExecutionRouter().decide("Cam-A1 支持什么协议？")

    assert decision.mode is ExecutionMode.WORKFLOW
    assert decision.reason_code == "deterministic_default"


def test_router_selects_agent_only_for_observation_dependent_request():
    decision = ExecutionRouter().decide(
        "先查出错误码 E1001 对应的产品，再告诉我这个产品的保修政策"
    )

    assert decision.mode is ExecutionMode.AGENT
    assert decision.reason_code == "observation_dependent_reference"


def test_task_state_can_only_be_succeeded_by_application_verification():
    state = TaskState(task_id="run-1", conversation_id=7, objective="查保修")

    with pytest.raises(ValueError, match="verification"):
        state.transition(TaskStatus.SUCCEEDED)

    state.transition(TaskStatus.VERIFYING)
    state.transition(TaskStatus.SUCCEEDED, verified=True)

    assert state.status is TaskStatus.SUCCEEDED


def test_tool_result_rejects_error_without_error_code():
    with pytest.raises(ValidationError, match="error_code"):
        ToolResult(status=ToolStatus.ERROR, retryable=False)


def test_verifier_rejects_evidence_not_observed_in_current_run():
    state = TaskState(
        task_id="run-1",
        conversation_id=7,
        objective="查保修",
        evidence_refs=["error_codes:1"],
        status=TaskStatus.VERIFYING,
    )
    candidate = AgentCandidate(
        answer="Cam-A1 保修一年。",
        intent="agent_dynamic_task",
        source_type="knowledge_graph",
        evidence_refs=["neo4j:warranty:Cam-A1"],
    )

    result = CandidateVerifier().verify(candidate, state)

    assert result.accepted is False
    assert result.error_code == "UNOBSERVED_EVIDENCE"


def test_verifier_rejects_factual_candidate_without_evidence_or_with_missing_information():
    state = TaskState(
        task_id="run-1",
        conversation_id=7,
        objective="查保修",
        evidence_refs=["error_codes:1"],
        missing_information=["product_name"],
        status=TaskStatus.VERIFYING,
    )
    no_evidence = AgentCandidate(
        answer="Cam-A1 保修一年。",
        source_type="knowledge_graph",
        evidence_refs=[],
    )
    unresolved_input = AgentCandidate(
        answer="Cam-A1 保修一年。",
        source_type="knowledge_graph",
        evidence_refs=["error_codes:1"],
    )

    assert CandidateVerifier().verify(no_evidence, state).error_code == "EVIDENCE_REQUIRED"
    assert CandidateVerifier().verify(unresolved_input, state).error_code == "MISSING_INFORMATION"


@pytest.mark.parametrize(
    ("tool_error", "error_code", "expected"),
    [
        ("TOOL_NOT_ALLOWED", "AGENT_RUNTIME_ERROR", FailureCategory.WRONG_TOOL),
        ("INVALID_TOOL_ARGUMENTS", "AGENT_RUNTIME_ERROR", FailureCategory.WRONG_TOOL_ARGS),
        ("TOOL_EXECUTION_ERROR", "AGENT_RUNTIME_ERROR", FailureCategory.TOOL_OR_RETRIEVAL),
        (None, "AGENT_RUNTIME_ERROR", FailureCategory.UNATTRIBUTED),
        (None, "RUNTIME_NO_CANDIDATE", FailureCategory.UNATTRIBUTED),
    ],
)
def test_failure_attribution_uses_only_deterministic_trace_evidence(
    tool_error, error_code, expected
):
    state = TaskState(task_id="run-1", conversation_id=7, objective="查保修")
    if tool_error:
        state.observe(
            "graph_lookup",
            {"operation": "warranty", "product_a": "Cam-A1"},
            ToolResult(status=ToolStatus.ERROR, error_code=tool_error),
        )

    assert FailureAttributor().attribute(state, error_code) is expected
