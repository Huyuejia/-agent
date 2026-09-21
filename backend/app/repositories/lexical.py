"""SQLAlchemyLexicalRepository：基于 PostgreSQL search_vector 的词法检索。

使用 tsquery（websearch_to_tsquery，安全转义）+ ts_rank_cd 排序。
多 token 默认采用 OR 召回语义（" or " 连接），再按 ts_rank_cd 排序。
所有查询使用参数绑定，不拼接用户原始输入，不做模糊 / LIKE / 向量 / 图谱 / LLM。
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.retrieval.lexical_repository import LexicalMatch, LexicalRepository

_SQL = text(
    """
    SELECT
        c.id AS chunk_id,
        c.document_id AS document_id,
        c.chunk_index AS chunk_index,
        c.content AS content,
        c.location AS location,
        d.filename AS document_name,
        ts_rank_cd(c.search_vector, websearch_to_tsquery('simple', :q)) AS score
    FROM document_chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.search_vector @@ websearch_to_tsquery('simple', :q)
    ORDER BY score DESC, c.document_id, c.chunk_index
    LIMIT :top_k
    """
)


class SQLAlchemyLexicalRepository(LexicalRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def search(self, tokens: Sequence[str], top_k: int) -> list[LexicalMatch]:
        if not tokens:
            return []
        query_text = " or ".join(tokens)
        rows = self._session.execute(
            _SQL, {"q": query_text, "top_k": top_k}
        ).fetchall()
        return [
            LexicalMatch(
                chunk_id=str(row.chunk_id),
                document_id=row.document_id,
                chunk_index=row.chunk_index,
                content=row.content,
                location=row.location,
                document_name=row.document_name,
                score=float(row.score),
            )
            for row in rows
        ]
