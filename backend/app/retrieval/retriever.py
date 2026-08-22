"""Retriever 协议：所有检索器的统一接口。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.retrieval.domain import Evidence, RetrievalStep


@runtime_checkable
class Retriever(Protocol):
    """检索器协议。

    query 为原始用户查询；step 为当前检索步骤（其 query 可能已被
    实体解析改写）。返回统一 Evidence 列表。
    """

    def retrieve(self, query: str, step: RetrievalStep) -> list[Evidence]:
        ...
