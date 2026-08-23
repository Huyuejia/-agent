"""LexicalRepository 协议：词法（全文）检索仓储。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class LexicalMatch:
    """一次词法检索命中的文档 chunk。"""

    chunk_id: str
    document_id: int
    chunk_index: int
    content: str
    location: str
    document_name: str
    score: float  # ts_rank_cd，越高越相关


class LexicalRepository(Protocol):
    """词法检索仓储协议。

    tokens 为已分词的词元列表（来自 Tokenizer）。返回按相关度降序的命中。
    """

    def search(self, tokens: Sequence[str], top_k: int) -> list[LexicalMatch]:
        ...
