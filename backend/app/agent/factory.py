"""Production composition for the controlled Agent task service."""

from pathlib import Path

from app.agent.runtime import SubprocessPiRuntimeClient
from app.agent.service import AgentTaskService
from app.agent.tools import AgentToolAdapter
from app.config import settings


class _KnowledgeSearchAdapter:
    def __init__(self, retrieval_service) -> None:
        self._retrieval = retrieval_service

    def search(self, query: str, top_k: int = 3) -> dict:
        result = self._retrieval.answer(query)
        return {
            "sources": [
                source.model_dump() for source in result["sources"][:top_k]
            ]
        }


def create_agent_task_service(retrieval_service) -> AgentTaskService:
    project_root = Path(__file__).resolve().parents[3]
    runtime_entry = project_root / "agent-runtime" / "dist" / "index.js"
    runtime = SubprocessPiRuntimeClient(
        command=[settings.pi_node_command, str(runtime_entry)],
        provider=settings.pi_model_provider,
        model=settings.pi_model,
        timeout_seconds=settings.pi_runtime_timeout_seconds,
    )

    def tool_factory(db):
        return AgentToolAdapter(
            exact_repository=retrieval_service.exact_repository_factory(db),
            graph_service=retrieval_service.graph_service,
            knowledge_search=_KnowledgeSearchAdapter(retrieval_service),
        )

    return AgentTaskService(
        runtime=runtime,
        tool_adapter_factory=tool_factory,
        max_tool_calls=settings.agent_max_tool_calls,
        max_tool_retries=settings.agent_max_tool_retries,
        max_verification_repairs=settings.agent_max_verification_repairs,
    )
