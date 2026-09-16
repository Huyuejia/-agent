"""Real Pi core loop through the existing authenticated /api/chat endpoint."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.runtime import SubprocessPiRuntimeClient
from app.agent.service import AgentTaskService
from app.agent.tools import AgentToolAdapter
from app.models.agent_run import AgentRun, AgentTraceEvent
from app.models.base import Base
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.postgres_database import get_postgres_db
from tests.test_agent_chat_api import FixtureExact, FixtureGraph, FixtureKnowledge


pytestmark = pytest.mark.integration


def test_real_pi_loop_completes_dynamic_task_through_chat_api():
    node = shutil.which("node")
    project_root = Path(__file__).resolve().parents[2]
    runtime_dir = project_root / "agent-runtime"
    entry = runtime_dir / "dist" / "index.js"
    pi_package = runtime_dir / "node_modules" / "@earendil-works" / "pi-agent-core"
    if not node or not entry.exists() or not pi_package.exists():
        pytest.skip("run `cd agent-runtime && npm ci && npm run build` for Pi integration")

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
        email="pi-api@example.com",
        normalized_email="pi-api@example.com",
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

    runtime = SubprocessPiRuntimeClient(
        command=[node, str(entry)],
        provider="faux",
        model="faux",
        scripted_scenario="error-code-warranty",
    )
    service = AgentTaskService(
        runtime=runtime,
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
            conversation_id = client.post(
                "/api/conversations?title=Pi动态任务"
            ).json()["conversation_id"]
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
    result = response.json()
    assert result["execution_mode"] == "agent"
    assert result["task_status"] == "SUCCEEDED"
    assert "Cam-A1" in result["answer"]

    db = sessions()
    try:
        run = db.get(AgentRun, result["agent_run_id"])
        observations = run.task_state["tool_observations"]
        assert [item["tool_name"] for item in observations] == [
            "exact_lookup",
            "graph_lookup",
        ]
        observed_sku = observations[0]["result"]["data"]["entities"][0][
            "attributes"
        ]["product_sku"]
        assert observations[1]["arguments"]["product_a"] == observed_sku
    finally:
        db.close()
