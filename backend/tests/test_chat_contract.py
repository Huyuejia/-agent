"""Public chat response contract tests."""

from app.schemas.conversation import ChatResponse, MessageSource


def test_chat_response_exposes_agent_fields_and_exact_sources():
    response = ChatResponse(
        conversation_id=7,
        answer="已找到精确记录。",
        intent="exact_lookup",
        confidence=1.0,
        source_type="exact",
        sources=[
            MessageSource(source_type="exact", relation="ORD123456")
        ],
        handoff_required=False,
        execution_mode="agent",
        agent_run_id="run-123",
        task_status="SUCCEEDED",
        needs_user_input=False,
        requested_fields=[],
    )

    assert response.execution_mode == "agent"
    assert response.agent_run_id == "run-123"
    assert response.task_status == "SUCCEEDED"
    assert response.needs_user_input is False
    assert response.requested_fields == []
    assert response.sources[0].source_type == "exact"


def test_chat_response_does_not_expose_internal_failure_category():
    response = ChatResponse(
        conversation_id=7,
        answer="处理失败。",
        intent="agent_failure",
        confidence=0.0,
        source_type="fallback",
        sources=[],
        handoff_required=True,
        execution_mode="agent",
        task_status="FAILED",
        failure_category="TOOL_OR_RETRIEVAL",
    )

    assert "failure_category" not in response.model_dump()
