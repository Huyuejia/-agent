"""Agent task orchestration and persistence tests."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.agent.domain import AgentCandidate, RuntimeResult
from app.agent.service import AgentTaskService
from app.agent.tools import AgentToolAdapter
from app.models.agent_run import AgentRun, AgentTraceEvent
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.user import User
from app.retrieval.exact_repository import ResolvedEntity


class FixtureExactRepository:
    def resolve(self, entity_types, entity_values):
        assert entity_types == ["error_code"]
        assert entity_values == ["E1001"]
        return [
            ResolvedEntity(
                entity_type="error_code",
                normalized_value="E1001",
                record_id="error-1",
                display_identifier="E1001",
                table="error_codes",
                attributes={"product_sku": "Cam-A1"},
            )
        ]


class FixtureGraph:
    def get_warranty(self, product_name):
        assert product_name == "Cam-A1"
        return {
            "policy_name": "标准保修",
            "duration": "1 年",
            "description": "非人为损坏免费维修。",
        }

    def get_protocols(self, product_name):
        return []

    def check_compatibility(self, product_a, product_b):
        return False


class UnusedKnowledgeSearch:
    def search(self, query, top_k=3):
        raise AssertionError("knowledge_search should not run")


class ObservationDrivenRuntime:
    """Test double with two model turns; turn two reads turn one's observation."""

    runtime_version = "test-observation-model"

    def run(self, *, objective, task_state, tool_schemas, execute_tool, emit_event):
        assert set(tool_schemas) == {
            "exact_lookup",
            "graph_lookup",
            "knowledge_search",
        }
        emit_event("model_action", {"turn": 1, "action": "exact_lookup"})
        first = execute_tool(
            "exact_lookup",
            {"entity_type": "error_code", "identifier": "E1001"},
        )
        observed_sku = first.data["entities"][0]["attributes"]["product_sku"]
        emit_event(
            "model_action",
            {"turn": 2, "action": "graph_lookup", "observed_sku": observed_sku},
        )
        second = execute_tool(
            "graph_lookup",
            {"operation": "warranty", "product_a": observed_sku},
        )
        return RuntimeResult(
            candidate=AgentCandidate(
                answer=second.evidence[0].text,
                source_type="knowledge_graph",
                evidence_refs=[item.evidence_id for item in second.evidence],
            )
        )


def _database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Conversation.__table__,
            AgentRun.__table__,
            AgentTraceEvent.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def _tool_factory(_db):
    return AgentToolAdapter(
        exact_repository=FixtureExactRepository(),
        graph_service=FixtureGraph(),
        knowledge_search=UnusedKnowledgeSearch(),
    )


def test_agent_service_persists_verified_state_and_ordered_trace():
    db = _database()
    user = User(
        email="agent@example.com",
        normalized_email="agent@example.com",
        password_hash="test-only",
    )
    db.add(user)
    db.flush()
    conversation = Conversation(user_id=user.id, title="dynamic")
    db.add(conversation)
    db.commit()

    service = AgentTaskService(
        runtime=ObservationDrivenRuntime(),
        tool_adapter_factory=_tool_factory,
    )
    result = service.execute(
        objective="先查出错误码 E1001 对应的产品，再告诉我这个产品的保修政策",
        conversation_id=conversation.id,
        user_id=user.id,
        request_id="request-1",
        boundary_reason="observation_dependent_reference",
        db=db,
    )

    assert result["execution_mode"] == "agent"
    assert result["task_status"] == "SUCCEEDED"
    assert result["agent_run_id"]
    assert "Cam-A1" in result["answer"]

    run = db.get(AgentRun, result["agent_run_id"])
    assert run.status == "SUCCEEDED"
    assert set(run.task_state["evidence_refs"]) == {
        "error_codes:error-1",
        "neo4j:warranty:Cam-A1",
    }
    events = db.scalars(
        select(AgentTraceEvent)
        .where(AgentTraceEvent.run_id == run.id)
        .order_by(AgentTraceEvent.sequence_number)
    ).all()
    assert [event.sequence_number for event in events] == list(
        range(1, len(events) + 1)
    )
    assert [event.event_type for event in events].count("tool_observation") == 2
    assert events[-1].event_type == "verification_accepted"


class UnsupportedCandidateRuntime:
    runtime_version = "test-invalid-candidate"

    def run(self, **kwargs):
        return RuntimeResult(
            candidate=AgentCandidate(
                answer="没有工具依据的回答",
                source_type="knowledge_graph",
                evidence_refs=["fabricated:1"],
            )
        )


def test_agent_cannot_mark_run_succeeded_with_fabricated_evidence():
    db = _database()
    user = User(
        email="reject@example.com",
        normalized_email="reject@example.com",
        password_hash="test-only",
    )
    db.add(user)
    db.flush()
    conversation = Conversation(user_id=user.id, title="reject")
    db.add(conversation)
    db.commit()
    service = AgentTaskService(
        runtime=UnsupportedCandidateRuntime(),
        tool_adapter_factory=_tool_factory,
    )

    result = service.execute(
        objective="dynamic",
        conversation_id=conversation.id,
        user_id=user.id,
        request_id="request-2",
        boundary_reason="test",
        db=db,
    )

    assert result["task_status"] == "FAILED"
    assert result["handoff_required"] is True
    run = db.get(AgentRun, result["agent_run_id"])
    assert run.failure_category == "UNOBSERVED_EVIDENCE"
