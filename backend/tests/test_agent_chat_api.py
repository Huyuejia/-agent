"""Authenticated /api/chat seam for an observation-dependent Agent task."""

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
from app.retrieval.exact_repository import ResolvedEntity


class FixtureExact:
    def resolve(self, entity_types, entity_values):
        return [
            ResolvedEntity(
                entity_type="error_code",
                normalized_value="E1001",
                record_id="fixture-error-1",
                display_identifier="E1001",
                table="error_codes",
                attributes={"product_sku": "Cam-A1"},
            )
        ]


class FixtureGraph:
    def get_warranty(self, product_name):
        return {
            "policy_name": "标准保修",
            "duration": "1 年",
            "description": "非人为损坏免费维修。",
        }

    def get_protocols(self, product_name):
        return ["WiFi", "Zigbee"]

    def check_compatibility(self, product_a, product_b):
        return False


class FixtureKnowledge:
    def search(self, query, top_k=3):
        return []


class ObservationDependentModel:
    runtime_version = "api-fixture-model"

    def run(self, *, execute_tool, emit_event, **_kwargs):
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
                evidence_refs=[second.evidence[0].evidence_id],
            )
        )


def test_existing_chat_endpoint_runs_dynamic_task_and_persists_messages():
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
        email="api-agent@example.com",
        normalized_email="api-agent@example.com",
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
        runtime=ObservationDependentModel(),
        tool_adapter_factory=lambda _db: AgentToolAdapter(
            exact_repository=FixtureExact(),
            graph_service=FixtureGraph(),
            knowledge_search=FixtureKnowledge(),
        ),
    )
    conversations_api._orchestrator = ChatOrchestrator(
        agent_service=service,
    )
    app = create_app(initialize_database=False)
    app.dependency_overrides[get_postgres_db] = override_db

    try:
        with TestClient(app) as client:
            client.headers.update(
                {"Authorization": f"Bearer {create_access_token(user_id)}"}
            )
            created = client.post("/api/conversations?title=动态任务")
            conversation_id = created.json()["conversation_id"]
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation_id,
                    "message": (
                        "先查出错误码 E1001 对应的产品，"
                        "再告诉我这个产品的保修政策"
                    ),
                },
            )
    finally:
        conversations_api._orchestrator = None
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_mode"] == "agent"
    assert body["task_status"] == "SUCCEEDED"
    assert body["agent_run_id"]
    assert "Cam-A1" in body["answer"]

    db = sessions()
    try:
        messages = db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        ).all()
        assert [message.role for message in messages] == ["user", "assistant"]
        run = db.get(AgentRun, body["agent_run_id"])
        assert run.status == "SUCCEEDED"
    finally:
        db.close()
