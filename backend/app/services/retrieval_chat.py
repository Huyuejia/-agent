"""统一检索聊天服务：QueryAnalyzer → RetrievalExecutor → Evidence 回答。"""

from __future__ import annotations

import json
import logging
from typing import Callable, Protocol

from sqlalchemy.orm import Session

from app.embeddings.base import Embedder
from app.repositories.exact import SQLAlchemyExactRepository
from app.repositories.lexical import SQLAlchemyLexicalRepository
from app.repositories.vector import SQLAlchemyVectorRepository
from app.retrieval.domain import Evidence, FusionStrategy, RetrievalMode, RetrievalPlan
from app.retrieval.exact_repository import ExactRepository
from app.retrieval.exact_retriever import ExactRetriever
from app.retrieval.executor import (
    ExecutionStatus,
    RetrievalExecutionError,
    RetrievalExecutionResult,
    RetrievalExecutor,
)
from app.retrieval.graph_retriever import GraphQueryService, GraphRetriever
from app.retrieval.lexical_retriever import LexicalRetriever
from app.retrieval.query_analyzer import QueryAnalyzer
from app.retrieval.tokenizer import JiebaTokenizer, Tokenizer
from app.retrieval.vector_retriever import PgVectorRetriever
from app.schemas.conversation import MessageSource
from app.services.llm_client import LLMError

logger = logging.getLogger(__name__)


class CompletionClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str: ...


class RetrievalChatResult(dict):
    """兼容现有 ChatOrchestrator 字典响应的显式类型标记。"""


class EvidenceAnswerer:
    """只根据检索执行结果生成答案；LLM 不参与路由或数据库查询。"""

    def __init__(self, llm_client: CompletionClient | None = None) -> None:
        self._llm = llm_client

    def answer(
        self,
        query: str,
        plan: RetrievalPlan,
        execution: RetrievalExecutionResult,
    ) -> RetrievalChatResult:
        if execution.status is ExecutionStatus.NEEDS_CLARIFICATION:
            return self._result(
                execution.clarification_question or "请补充具体查询条件。",
                plan,
                source_type="clarification",
                sources=[],
                handoff_required=False,
            )
        if execution.status is ExecutionStatus.NEEDS_CONTEXT:
            return self._result(
                "当前消息只有指代信息，请重新提供具体商品名称或型号。",
                plan,
                source_type="clarification",
                sources=[],
                handoff_required=False,
            )

        evidence = execution.evidence
        sources = self._sources(evidence)
        source_type = self._source_type(evidence, plan)

        if not evidence:
            if plan.intent == "exact_lookup":
                answer = "未找到该精确标识符对应的记录；为避免误匹配，未降级为相似度检索。"
                handoff_required = False
            else:
                answer = "没有检索到足够证据，建议补充更具体的信息或联系人工客服。"
                handoff_required = True
            return self._result(
                answer,
                plan,
                source_type=source_type,
                sources=sources,
                handoff_required=handoff_required,
            )

        answer = self._deterministic_answer(evidence)
        if self._llm is not None:
            try:
                answer = self._llm.complete(self._messages(query, evidence))
            except LLMError:
                logger.warning("LLM generation failed; using Evidence fallback", exc_info=True)

        return self._result(
            answer,
            plan,
            source_type=source_type,
            sources=sources,
            handoff_required=False,
        )

    @staticmethod
    def _messages(query: str, evidence: list[Evidence]) -> list[dict[str, str]]:
        serialized = [
            {
                "evidence_id": item.evidence_id,
                "text": item.text,
                "citation": item.citation.model_dump(),
            }
            for item in evidence
        ]
        return [
            {
                "role": "system",
                "content": (
                    "你是智能客服回答器。只能使用给定 Evidence 回答，"
                    "不得补充证据之外的事实；结论后用 [evidence_id] 标注来源。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题：{query}\n"
                    f"Evidence：{json.dumps(serialized, ensure_ascii=False)}"
                ),
            },
        ]

    @staticmethod
    def _deterministic_answer(evidence: list[Evidence]) -> str:
        return "\n".join(
            f"{index}. {item.text} [{item.evidence_id}]"
            for index, item in enumerate(evidence, start=1)
        )

    @staticmethod
    def _source_type(evidence: list[Evidence], plan: RetrievalPlan) -> str:
        modes = {item.kind for item in evidence}
        if RetrievalMode.GRAPH in modes:
            return "knowledge_graph"
        if modes & {RetrievalMode.LEXICAL, RetrievalMode.VECTOR, RetrievalMode.HYBRID}:
            return "document_rag"
        if RetrievalMode.EXACT in modes or plan.intent == "exact_lookup":
            return "exact"
        return "fallback"

    @staticmethod
    def _sources(evidence: list[Evidence]) -> list[MessageSource]:
        sources: list[MessageSource] = []
        seen: set[str] = set()
        for item in evidence:
            if item.evidence_id in seen:
                continue
            seen.add(item.evidence_id)
            payload = item.citation.payload
            if item.kind in {
                RetrievalMode.LEXICAL,
                RetrievalMode.VECTOR,
                RetrievalMode.HYBRID,
            }:
                sources.append(
                    MessageSource(
                        source_type="document_rag",
                        document_name=payload.get("document_name"),
                        location=payload.get("location"),
                        snippet=item.text,
                    )
                )
            elif item.kind is RetrievalMode.GRAPH:
                sources.append(
                    MessageSource(
                        source_type="knowledge_graph",
                        relation=item.text,
                    )
                )
            else:
                sources.append(
                    MessageSource(
                        source_type="exact",
                        relation=item.text,
                    )
                )
        return sources

    @staticmethod
    def _result(
        answer: str,
        plan: RetrievalPlan,
        *,
        source_type: str,
        sources: list[MessageSource],
        handoff_required: bool,
    ) -> RetrievalChatResult:
        return RetrievalChatResult(
            answer=answer,
            intent=plan.intent,
            confidence=plan.confidence,
            source_type=source_type,
            sources=sources,
            handoff_required=handoff_required,
        )


class RetrievalChatService:
    """每次请求使用独立 PostgreSQL Session 执行统一检索计划。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        embedder: Embedder,
        graph_service: GraphQueryService,
        llm_client: CompletionClient | None = None,
        analyzer: QueryAnalyzer | None = None,
        tokenizer: Tokenizer | None = None,
        exact_repository_factory: Callable[[Session], ExactRepository] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._graph = graph_service
        self.graph_service = graph_service
        self._answerer = EvidenceAnswerer(llm_client)
        self._analyzer = analyzer or QueryAnalyzer()
        self._tokenizer = tokenizer or JiebaTokenizer()
        self._exact_repository_factory = (
            exact_repository_factory or SQLAlchemyExactRepository
        )
        self.exact_repository_factory = self._exact_repository_factory

    def answer(self, query: str) -> RetrievalChatResult:
        plan = self._analyzer.analyze(query)
        execution = self._execute(query, plan)
        return self._answerer.answer(query, plan, execution)

    def retrieve(self, query: str) -> RetrievalExecutionResult:
        """Execute retrieval without generating an answer for Agent tools."""
        plan = self._analyzer.analyze(query)
        return self._execute(query, plan)

    def _execute(
        self,
        query: str,
        plan: RetrievalPlan,
    ) -> RetrievalExecutionResult:
        if not plan.steps:
            execution = RetrievalExecutor({}).execute(query, plan)
            return execution

        session = self._session_factory()
        try:
            executor = RetrievalExecutor(
                {
                    RetrievalMode.EXACT: ExactRetriever(
                        self._exact_repository_factory(session)
                    ),
                    RetrievalMode.GRAPH: GraphRetriever(self._graph),
                    RetrievalMode.LEXICAL: LexicalRetriever(
                        SQLAlchemyLexicalRepository(session),
                        self._tokenizer,
                    ),
                    RetrievalMode.VECTOR: PgVectorRetriever(
                        SQLAlchemyVectorRepository(session),
                        self._embedder.embed,
                        embedding_model=self._embedder.model_name,
                        embedding_version=self._embedder.model_version,
                        dimension=self._embedder.dimension,
                    ),
                }
            )
            try:
                execution = executor.execute(query, plan)
            except RetrievalExecutionError:
                if plan.fusion_strategy is not FusionStrategy.RRF:
                    raise
                logger.warning(
                    "Hybrid retrieval failed; retrying lexical-only",
                    exc_info=True,
                )
                lexical_plan = plan.model_copy(
                    update={
                        "steps": [
                            step
                            for step in plan.steps
                            if step.mode is RetrievalMode.LEXICAL
                        ],
                        "fusion_strategy": None,
                    }
                )
                execution = executor.execute(query, lexical_plan)
        finally:
            session.close()
        return execution


def create_retrieval_chat_service() -> RetrievalChatService:
    """按应用配置创建生产检索服务；BGE 权重仍在首次向量查询时懒加载。"""
    from neo4j import GraphDatabase

    from app.config import settings
    from app.embeddings.bge_m3 import create_bge_m3_embedder
    from app.postgres_database import get_postgres_session_factory
    from app.services.graph_service import GraphService
    from app.services.llm_client import LLMClient

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    llm_client = None
    if settings.llm_base_url:
        llm_client = LLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )

    from app.repositories.exact_cache import (
        RedisExactRepositoryCache,
        create_redis_client,
    )

    def exact_repository_factory(session: Session) -> ExactRepository:
        return RedisExactRepositoryCache(
            repository=SQLAlchemyExactRepository(session),
            redis_client_factory=create_redis_client,
            positive_ttl_seconds=settings.redis_exact_cache_ttl_seconds,
            negative_ttl_seconds=settings.redis_exact_cache_negative_ttl_seconds,
        )

    return RetrievalChatService(
        session_factory=get_postgres_session_factory(),
        embedder=create_bge_m3_embedder(),
        graph_service=GraphService(driver),
        llm_client=llm_client,
        exact_repository_factory=exact_repository_factory,
    )
