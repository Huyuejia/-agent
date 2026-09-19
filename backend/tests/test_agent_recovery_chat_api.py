"""The authenticated chat seam resumes a waiting Agent task."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.domain import AgentCandidate, RuntimeResult
from app.agent.service import AgentTaskService
from app.agent.tools import AgentToolAdapter
from app.models.agent_run import AgentRun, AgentTraceEvent
from app.models.base import Base
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.postgres_database import get_postgres_db
from tests.test_agent_chat_api import FixtureExact, FixtureGraph, FixtureKnowledge


class WaitingThenCompleteModel:
    runtime_version = "api-waiting-model"

    def run(self, *, task_state, execute_tool, **_kwargs):
        if not task_state.known_facts:
            return RuntimeResult(
                clarification_text="请提供需要查询保修的产品名称。",
                requested_fields=["product_name"],
            )
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


def test_chat_endpoint_resumes_waiting_task_before_new_routing_decision():
    from app.main import create_app
    from app.security.jwt import create_access_token
    from app.services.chat_orchestrator import ChatOrchestrator
    import app.api.conversations as conversations_api

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Conversation.__table__,
            Message.__table__,
            AgentRun.__table__,
            AgentTraceEvent.__table__,
        ],
    )
    sessions = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = sessions()
    user = User(
        email="waiting-api@example.com",
        normalized_email="waiting-api@example.com",
        password_hash="test-only",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    user_id = user.id
    db.close()

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    service = AgentTaskService(
        runtime=WaitingThenCompleteModel(),
        tool_adapter_factory=lambda _db: AgentToolAdapter(
            exact_repository=FixtureExact(),
            graph_service=FixtureGraph(),
            knowledge_search=FixtureKnowledge(),
        ),
    )
    conversations_api._orchestrator = ChatOrchestrator(
        graph_service=FixtureGraph(),
        rag_service=FixtureKnowledge(),
        agent_service=service,
    )
    app = create_app(initialize_database=False)
    app.dependency_overrides[get_postgres_db] = override_db

    try:
        with TestClient(app) as client:
            client.headers.update(
                {"Authorization": f"Bearer {create_access_token(user_id)}"}
            )
            conversation_id = client.post("/api/conversations").json()["conversation_id"]
            waiting = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation_id,
                    "message": "先查出错误码 E1001 对应的产品，再告诉我保修政策",
                },
            )
            resumed = client.post(
                "/api/chat",
                json={"conversation_id": conversation_id, "message": "Cam-A1"},
            )
    finally:
        conversations_api._orchestrator = None
        app.dependency_overrides.clear()

    assert waiting.status_code == 200, waiting.text
    waiting_body = waiting.json()
    assert waiting_body["task_status"] == "WAITING_FOR_USER"
    assert waiting_body["needs_user_input"] is True
    assert waiting_body["requested_fields"] == ["product_name"]
    assert resumed.status_code == 200, resumed.text
    resumed_body = resumed.json()
    assert resumed_body["task_status"] == "SUCCEEDED"
    assert resumed_body["agent_run_id"] == waiting_body["agent_run_id"]

    db = sessions()
    try:
        run = db.get(AgentRun, resumed_body["agent_run_id"])
        assert run.objective == "先查出错误码 E1001 对应的产品，再告诉我保修政策"
        assert run.task_state["known_facts"] == [{"source": "user", "fields": {"product_name": "Cam-A1"}}]
        messages = db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        ).all()
        assert [message.role for message in messages] == ["user", "assistant"] * 2
    finally:
        db.close()
