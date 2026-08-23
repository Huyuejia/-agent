"""
RAG 服务：BGE-M3 本地向量化 + Chroma 持久化检索。

- 模型路径通过 BGE_MODEL_PATH 配置，local_files_only=True 绝不联网
- 维度从模型实例动态读取，不写死
- 构造时可注入 embed 回调，测试用 fake embedding
"""

import os
import uuid
from collections.abc import Callable
from typing import Optional

import chromadb
from chromadb.api.types import EmbeddingFunction as ChromaEmbedFn

from app.config import settings
from app.embeddings import create_bge_m3_embedder

# ---------------------------------------------------------------------------
# 类型
# ---------------------------------------------------------------------------
# embed 回调：接收文本列表，返回向量列表
EmbedFn = Callable[[list[str]], list[list[float]]]


# ---------------------------------------------------------------------------
# 真实的 BGE-M3 加载
# ---------------------------------------------------------------------------
def _load_bge_m3_embedder() -> tuple[EmbedFn, int]:
    """创建共享契约的懒加载 Embedder；此调用本身不加载模型权重。"""
    embedder = create_bge_m3_embedder()
    return embedder.embed, embedder.dimension


# ---------------------------------------------------------------------------
# Chroma 适配的 embedding function
# ---------------------------------------------------------------------------
class _ChromaEmbeddingAdapter:
    """将 EmbedFn 包装为 Chroma 可用的 EmbeddingFunction。"""

    def __init__(self, embed_fn: EmbedFn) -> None:
        self._embed_fn = embed_fn

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self._embed_fn(input)


# ---------------------------------------------------------------------------
# RagService
# ---------------------------------------------------------------------------
class RagService:
    """
    文档检索服务。

    使用方式:
        # 生产
        rag = RagService()

        # 测试 (fake embedding)
        rag = RagService(embed_fn=fake_fn, embed_dim=1024)
    """

    def __init__(
        self,
        embed_fn: Optional[EmbedFn] = None,
        embed_dim: Optional[int] = None,
        persist_dir: Optional[str] = None,
    ) -> None:
        # ---- embedding ----
        if embed_fn is not None and embed_dim is not None:
            self._embed_fn = embed_fn
            self._embed_dim = embed_dim
        else:
            self._embed_fn, self._embed_dim = _load_bge_m3_embedder()

        # ---- Chroma ----
        chroma_dir = persist_dir or settings.chroma_persist_dir
        os.makedirs(chroma_dir, exist_ok=True)
        self._chroma_client = chromadb.PersistentClient(path=chroma_dir)
        self._collection = self._chroma_client.get_or_create_collection(
            name="document_chunks",
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def embed_dim(self) -> int:
        return self._embed_dim

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def add_chunks(
        self,
        texts: list[str],
        metadatas: list[dict],
        ids: Optional[list[str]] = None,
    ) -> list[str]:
        """
        向量化并写入 Chroma。
        返回 chroma_id 列表。
        """
        if not texts:
            return []

        if ids is None:
            ids = [f"chunk_{uuid.uuid4().hex[:16]}" for _ in texts]

        embeddings = self._embed_fn(texts)

        self._collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        return ids

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def search(self, query: str, top_k: int = 4) -> dict:
        """
        向量检索，返回 top_k 结果。

        返回格式:
            {
                "query": "...",
                "answer": "基于检索片段的模板回答",
                "source_type": "document_rag",
                "sources": [{"document_name": "...", "location": "...", "snippet": "..."}],
                "chunk_count": N
            }
        """
        if not query.strip():
            raise ValueError("查询文本不能为空")

        query_vec = self._embed_fn([query])

        result = self._collection.query(
            query_embeddings=query_vec,
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        # Chroma 返回嵌套列表
        ids_list: list[list[str]] = result.get("ids", [[]])
        docs_list: list[list[str]] = result.get("documents", [[]])
        metas_list: list[list[dict]] = result.get("metadatas", [[]])

        ids = ids_list[0] if ids_list else []
        docs = docs_list[0] if docs_list else []
        metas = metas_list[0] if metas_list else []

        sources = []
        for i, chunk_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            snippet = docs[i] if i < len(docs) else ""

            # 截断 snippet 至 200 字用于前端展示
            display_snippet = snippet[:200] + ("…" if len(snippet) > 200 else "")

            sources.append(
                {
                    "document_name": meta.get("document_name", "未知文档"),
                    "location": meta.get("location", "未知位置"),
                    "snippet": display_snippet,
                }
            )

        # 用模板将检索事实组织成回答（不接入 LLM）
        answer = _build_template_answer(query, sources)

        return {
            "query": query,
            "answer": answer,
            "source_type": "document_rag",
            "sources": sources,
            "chunk_count": len(sources),
        }


# ---------------------------------------------------------------------------
# 模板回答（本版本用模板，不接大模型）
# ---------------------------------------------------------------------------
def _build_template_answer(query: str, sources: list[dict]) -> str:
    if not sources:
        return f"未在已上传文档中找到与「{query}」相关的信息。"
    parts = [f"根据已上传的文档，关于「{query}」找到以下相关信息："]
    for i, src in enumerate(sources, 1):
        parts.append(f"{i}. [{src['document_name']} | {src['location']}] {src['snippet']}")
    parts.append("（以上信息来源于已上传文档，仅供参考。）")
    return "\n\n".join(parts)
