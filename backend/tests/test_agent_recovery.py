"""Public AgentTaskService recovery and bounded-failure behavior."""

from sqlalchemy import create_engine, select
import pytest
from sqlalchemy.orm import sessionmaker

from app.agent.domain import (
    AgentCandidate,
    RuntimeResult,
    TaskState,
    TaskStatus,
    ToolResult,
    ToolStatus,
)
from app.agent.service import AgentTaskService
from app.models.agent_run import AgentRun, AgentTraceEvent
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.user import User
from app.retrieval.domain import Citation, Evidence, RetrievalMode


def _database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Conversation.__table__, AgentRun.__table__, AgentTraceEvent.__table__],
    )
    return sessionmaker(bind=engine)()


def _context(db):
    user = User(email="recovery@example.com", normalized_email="recovery@example.com", password_hash="test-only")
    db.add(user)
    db.flush()
    conversation = Conversation(user_id=user.id, title="recovery")
    db.add(conversation)
    db.commit()
    return user, conversation


def _success_result():
    evidence = Evidence(
        evidence_id="neo4j:warranty:Cam-A1",
        kind=RetrievalMode.GRAPH,
        text="Cam-A1 适用标准保修。",
        citation=Citation(source_type="neo4j", payload={}),
    )
    return ToolResult(status=ToolStatus.OK, evidence=[evidence])


class ScriptedToolAdapter:
    tool_schemas = {"graph_lookup": {}}

    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def execute(self, _tool_name, _arguments):
        self.calls += 1
        return next(self.results)


class ClarificationThenCompletionRuntime:
    runtime_version = "test-clarification-model"

    def run(self, *, task_state, execute_tool, **_kwargs):
        if not task_state.known_facts:
            return RuntimeResult(
                clarification_text="请提供需要查询保修的产品名称。",
                requested_fields=["product_name"],
            )
        result = execute_tool("graph_lookup", {"operation": "warranty", "product_a": "Cam-A1"})
        return RuntimeResult(candidate=AgentCandidate(
            answer=result.evidence[0].text,
            source_type="knowledge_graph",
            evidence_refs=[result.evidence[0].evidence_id],
        ))


class OneToolRuntime:
    runtime_version = "test-tool-model"

    def run(self, *, execute_tool, **_kwargs):
        result = execute_tool("graph_lookup", {"operation": "warranty", "product_a": "Cam-A1"})
        if result.status is not ToolStatus.OK:
            return RuntimeResult(error_code="TOOL_UNAVAILABLE")
        return RuntimeResult(candidate=AgentCandidate(
            answer=result.evidence[0].text,
            source_type="knowledge_graph",
            evidence_refs=[result.evidence[0].evidence_id],
        ))


class TooManyToolCallsRuntime:
    runtime_version = "test-tool-limit-model"

    def run(self, *, execute_tool, **_kwargs):
        for _ in range(7):
            execute_tool("graph_lookup", {"operation": "warranty", "product_a": "Cam-A1"})
        return RuntimeResult(error_code="TOOL_CALL_LIMIT_EXCEEDED")


class ToolThenRuntimeFailure:
    runtime_version = "test-runtime-failure-model"

    def run(self, *, execute_tool, **_kwargs):
        execute_tool("graph_lookup", {"operation": "warranty", "product_a": "Cam-A1"})
        raise RuntimeError("transport unavailable")


class CandidateAfterToolLimitRuntime:
    runtime_version = "test-tool-limit-candidate-model"

    def run(self, *, execute_tool, **_kwargs):
        first = execute_tool(
            "graph_lookup", {"operation": "warranty", "product_a": "Cam-A1"}
        )
        execute_tool(
            "graph_lookup", {"operation": "protocols", "product_a": "Cam-A1"}
        )
        return RuntimeResult(
            candidate=AgentCandidate(
                answer=first.evidence[0].text,
                source_type="knowledge_graph",
                evidence_refs=[first.evidence[0].evidence_id],
            )
        )


def _service(runtime, results, **limits):
    adapter = ScriptedToolAdapter(results)
    return AgentTaskService(runtime=runtime, tool_adapter_factory=lambda _db: adapter, **limits), adapter


def _execute(service, db, user, conversation, objective="查询设备保修", **kwargs):
    return service.execute(
        objective=objective,
        conversation_id=conversation.id,
        user_id=user.id,
        request_id="request-recovery",
        boundary_reason="test",
        db=db,
        **kwargs,
    )


def test_waiting_task_resumes_from_persisted_state_without_replacing_objective():
    db = _database()
    user, conversation = _context(db)
    service, _adapter = _service(ClarificationThenCompletionRuntime(), [_success_result()])

    waiting = _execute(service, db, user, conversation)

    assert waiting["task_status"] == "WAITING_FOR_USER"
    assert waiting["needs_user_input"] is True
    assert waiting["requested_fields"] == ["product_name"]
    run = db.get(AgentRun, waiting["agent_run_id"])
    assert run.task_state["missing_information"] == ["product_name"]

    completed = _execute(
        service, db, user, conversation,
        objective="Cam-A1", resume_run_id=waiting["agent_run_id"],
    )

    assert completed["agent_run_id"] == waiting["agent_run_id"]
    assert completed["task_status"] == "SUCCEEDED"
    run = db.get(AgentRun, completed["agent_run_id"])
    assert run.objective == "查询设备保修"
    assert run.task_state["known_facts"] == [{"source": "user", "text": "Cam-A1"}]
    assert run.task_state["missing_information"] == []
    events = db.scalars(
        select(AgentTraceEvent)
        .where(AgentTraceEvent.run_id == completed["agent_run_id"])
        .order_by(AgentTraceEvent.sequence_number)
    ).all()
    assert [event.sequence_number for event in events] == list(range(1, len(events) + 1))
    assert any(
        event.event_type == "STATE_TRANSITION"
        and event.payload["from"] == "WAITING_FOR_USER"
        and event.payload["to"] == "RUNNING"
        for event in events
    )


def test_not_found_is_an_observation_without_an_identical_retry():
    db = _database()
    user, conversation = _context(db)
    service, adapter = _service(OneToolRuntime(), [ToolResult(status=ToolStatus.NOT_FOUND)])

    result = _execute(service, db, user, conversation)

    assert result["task_status"] == "FAILED"
    assert adapter.calls == 1
    assert "tool_retry" not in [event.event_type for event in db.scalars(select(AgentTraceEvent)).all()]


def test_retryable_tool_error_is_retried_once_and_recorded():
    db = _database()
    user, conversation = _context(db)
    error = ToolResult(status=ToolStatus.ERROR, error_code="TEMPORARY_RETRIEVAL_FAILURE", retryable=True)
    service, adapter = _service(OneToolRuntime(), [error, _success_result()], max_tool_retries=1)

    result = _execute(service, db, user, conversation)

    assert result["task_status"] == "SUCCEEDED"
    assert adapter.calls == 2
    run = db.get(AgentRun, result["agent_run_id"])
    assert list(run.task_state["retry_info"].values()) == [1]
    assert "RETRY" in [event.event_type for event in db.scalars(select(AgentTraceEvent)).all()]


def test_retryable_tool_error_stops_after_one_retry():
    db = _database()
    user, conversation = _context(db)
    error = lambda: ToolResult(status=ToolStatus.ERROR, error_code="TEMPORARY_RETRIEVAL_FAILURE", retryable=True)
    service, adapter = _service(OneToolRuntime(), [error(), error()], max_tool_retries=1)

    result = _execute(service, db, user, conversation)

    assert result["task_status"] == "FAILED"
    assert adapter.calls == 2


def test_tool_limit_and_runtime_failure_are_safe_and_evidence_preserving():
    db = _database()
    user, conversation = _context(db)
    service, adapter = _service(TooManyToolCallsRuntime(), [_success_result() for _ in range(6)], max_tool_calls=6)

    result = _execute(service, db, user, conversation)

    assert result["task_status"] == "FAILED"
    assert adapter.calls == 6
    assert any(event.error_code == "TOOL_CALL_LIMIT_EXCEEDED" for event in db.scalars(select(AgentTraceEvent)).all())

    db = _database()
    user, conversation = _context(db)
    service, _adapter = _service(ToolThenRuntimeFailure(), [_success_result()])
    result = _execute(service, db, user, conversation)

    assert result["task_status"] == "FAILED"
    run = db.get(AgentRun, result["agent_run_id"])
    assert run.task_state["evidence_refs"] == ["neo4j:warranty:Cam-A1"]
    assert run.failure_category == "UNATTRIBUTED"


def test_tool_limit_error_cannot_be_bypassed_by_an_evidence_backed_candidate():
    db = _database()
    user, conversation = _context(db)
    service, _adapter = _service(
        CandidateAfterToolLimitRuntime(), [_success_result()], max_tool_calls=1
    )

    result = _execute(service, db, user, conversation)

    assert result["task_status"] == "FAILED"
    run = db.get(AgentRun, result["agent_run_id"])
    assert run.failure_category == "VERIFICATION"
    assert run.task_state["tool_observations"][-1]["result"]["error_code"] == "TOOL_CALL_LIMIT_EXCEEDED"


def test_service_rejects_a_new_task_while_the_conversation_has_an_active_run():
    db = _database()
    user, conversation = _context(db)
    active_state = TaskState(
        task_id="active-run",
        conversation_id=conversation.id,
        objective="正在执行的任务",
    )
    db.add(
        AgentRun(
            id="active-run",
            conversation_id=conversation.id,
            user_id=user.id,
            request_id="active-request",
            objective=active_state.objective,
            execution_mode="agent",
            status=TaskStatus.RUNNING.value,
            task_state=active_state.model_dump(mode="json"),
            runtime_version="test",
        )
    )
    db.commit()
    service, _adapter = _service(OneToolRuntime(), [_success_result()])

    with pytest.raises(ValueError, match="active agent task"):
        _execute(service, db, user, conversation)


class VerificationRepairRuntime:
    runtime_version = "test-verification-repair-model"

    def __init__(self):
        self.repair_feedback = []

    def run(self, **_kwargs):
        return RuntimeResult(
            candidate=AgentCandidate(
                answer="未经证据支持的答案",
                source_type="knowledge_graph",
                evidence_refs=["fabricated:1"],
            )
        )

    def repair(self, *, verification, execute_tool, **_kwargs):
        self.repair_feedback.append(verification.error_code)
        result = execute_tool(
            "graph_lookup", {"operation": "warranty", "product_a": "Cam-A1"}
        )
        return RuntimeResult(
            candidate=AgentCandidate(
                answer=result.evidence[0].text,
                source_type="knowledge_graph",
                evidence_refs=[result.evidence[0].evidence_id],
            )
        )


def test_verification_rejection_gets_one_repair_and_records_replayable_trace():
    db = _database()
    user, conversation = _context(db)
    runtime = VerificationRepairRuntime()
    service, _adapter = _service(runtime, [_success_result()])

    result = _execute(service, db, user, conversation)

    assert result["task_status"] == "SUCCEEDED"
    assert runtime.repair_feedback == ["UNOBSERVED_EVIDENCE"]
    events = db.scalars(
        select(AgentTraceEvent)
        .where(AgentTraceEvent.run_id == result["agent_run_id"])
        .order_by(AgentTraceEvent.sequence_number)
    ).all()
    assert [event.sequence_number for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events].count("VERIFICATION_RESULT") == 2
    assert "RETRY" in [event.event_type for event in events]
    assert events[-1].event_type == "RUN_COMPLETED"


class RejectedVerificationRepairRuntime(VerificationRepairRuntime):
    def repair(self, **_kwargs):
        self.repair_feedback.append("second_attempt")
        return RuntimeResult(
            candidate=AgentCandidate(
                answer="仍然未经支持的答案",
                source_type="knowledge_graph",
                evidence_refs=["fabricated:2"],
            )
        )


def test_second_verification_rejection_returns_safe_fallback_without_success():
    db = _database()
    user, conversation = _context(db)
    runtime = RejectedVerificationRepairRuntime()
    service, _adapter = _service(runtime, [])

    result = _execute(service, db, user, conversation)

    assert result["task_status"] == "FAILED"
    assert result["source_type"] == "fallback"
    assert runtime.repair_feedback == ["second_attempt"]
    events = db.scalars(
        select(AgentTraceEvent)
        .where(AgentTraceEvent.run_id == result["agent_run_id"])
        .order_by(AgentTraceEvent.sequence_number)
    ).all()
    assert [event.event_type for event in events].count("VERIFICATION_RESULT") == 2
    assert events[-1].event_type == "RUN_COMPLETED"
