"""ExactRetriever：精确检索器，实现 Retriever 协议。

只做数据库等值查询，未命中返回空列表（由未来执行器按 MissPolicy.STOP 处理）。
不调用 pgvector / Neo4j / LLM；不对未命中的货号做模糊 / LIKE /
全文 / 向量查询。
"""

from __future__ import annotations

from app.retrieval.domain import Citation, Evidence, RetrievalMode, RetrievalStep
from app.retrieval.exact_repository import ExactRepository, ResolvedEntity


class ExactRetriever:
    """把 RetrievalStep.filters 中的显式标识符解析为 Evidence。"""

    def __init__(self, repository: ExactRepository) -> None:
        self._repository = repository

    def retrieve(self, query: str, step: RetrievalStep) -> list[Evidence]:
        entity_types = step.filters.get("entity_types", [])
        entity_values = step.filters.get("entity_values", [])
        resolved = self._repository.resolve(entity_types, entity_values)
        return [self._to_evidence(item) for item in resolved]

    @staticmethod
    def _to_evidence(item: ResolvedEntity) -> Evidence:
        return Evidence(
            evidence_id=f"{item.table}:{item.record_id}",
            kind=RetrievalMode.EXACT,
            text=item.display_identifier,
            citation=Citation(
                source_type=item.table,
                payload={
                    "table": item.table,
                    "record_id": item.record_id,
                    "identifier": item.display_identifier,
                },
            ),
            entity_ids=[item.record_id],
        )
