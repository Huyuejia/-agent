"""Embedding 抽象与本地模型实现。"""

from app.embeddings.base import Embedder, EmbeddingError
from app.embeddings.bge_m3 import BgeM3Embedder, create_bge_m3_embedder

__all__ = [
    "BgeM3Embedder",
    "Embedder",
    "EmbeddingError",
    "create_bge_m3_embedder",
]
