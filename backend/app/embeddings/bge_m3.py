"""本地 BGE-M3 的线程安全懒加载适配器。"""

from __future__ import annotations

import math
from numbers import Real
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from app.embeddings.base import EmbeddingError

ModelLoader = Callable[..., Any]


def _default_loader(
    model_path: str,
    *,
    local_files_only: bool,
    device: str,
) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
        resolved_device = device
        if device == "auto":
            import torch

            resolved_device = "cuda" if torch.cuda.is_available() else "cpu"

        return SentenceTransformer(
            model_path,
            local_files_only=local_files_only,
            trust_remote_code=False,
            device=resolved_device,
        )
    except Exception as exc:
        raise EmbeddingError(f"加载 BGE-M3 模型失败：{exc}") from exc


class BgeM3Embedder:
    """只有第一次非空 embed 调用才加载模型权重。"""

    def __init__(
        self,
        *,
        model_path: str | Path,
        model_name: str,
        model_version: str,
        dimension: int = 1024,
        device: str = "auto",
        batch_size: int = 4,
        local_files_only: bool = True,
        loader: ModelLoader | None = None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name 不能为空")
        if not model_version.strip():
            raise ValueError("model_version 不能为空")
        if dimension <= 0:
            raise ValueError("dimension 必须大于 0")
        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0")

        self._model_path = Path(model_path)
        self._model_name = model_name.strip()
        self._model_version = model_version.strip()
        self._dimension = dimension
        self._device = device
        self._batch_size = batch_size
        self._local_files_only = local_files_only
        self._loader = loader or _default_loader
        self._model: Any | None = None
        self._load_lock = Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("待向量化文本不得包含空文本")

        model = self._ensure_model()
        try:
            output = model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=self._batch_size,
            )
        except Exception as exc:
            raise EmbeddingError(f"BGE-M3 向量化失败：{exc}") from exc

        vectors = output.tolist() if hasattr(output, "tolist") else output
        return self._validate_output(vectors, expected_count=len(texts))

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            if not self._model_path.is_dir():
                raise EmbeddingError(f"BGE 模型路径不存在：{self._model_path}")
            model = self._loader(
                str(self._model_path),
                local_files_only=self._local_files_only,
                device=self._device,
            )
            dimension_getter = getattr(model, "get_embedding_dimension", None)
            if dimension_getter is None:
                # SentenceTransformers < 5.0
                dimension_getter = model.get_sentence_embedding_dimension
            actual_dimension = dimension_getter()
            if actual_dimension != self._dimension:
                raise EmbeddingError(
                    f"BGE 模型维度错误：期望 {self._dimension}，实际 {actual_dimension}"
                )
            self._model = model
            return model

    def _validate_output(
        self,
        vectors: Any,
        *,
        expected_count: int,
    ) -> list[list[float]]:
        if not isinstance(vectors, (list, tuple)) or len(vectors) != expected_count:
            actual = len(vectors) if isinstance(vectors, (list, tuple)) else "非序列"
            raise EmbeddingError(
                f"embedding 数量错误：期望 {expected_count}，实际 {actual}"
            )

        normalized: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, (list, tuple)) or len(vector) != self._dimension:
                actual = len(vector) if isinstance(vector, (list, tuple)) else "非序列"
                raise EmbeddingError(
                    f"embedding 维度错误：期望 {self._dimension}，实际 {actual}"
                )
            if not all(isinstance(value, Real) for value in vector):
                raise EmbeddingError("embedding 必须全部为数值")
            converted = [float(value) for value in vector]
            if not all(math.isfinite(value) for value in converted):
                raise EmbeddingError("embedding 包含非有限数值（NaN/Inf）")
            normalized.append(converted)
        return normalized


def create_bge_m3_embedder() -> BgeM3Embedder:
    """从应用配置创建 Embedder；创建本身不会加载模型。"""
    from app.config import settings

    return BgeM3Embedder(
        model_path=settings.bge_model_path,
        model_name=settings.bge_model_name,
        model_version=settings.bge_model_version,
        dimension=settings.bge_embedding_dimension,
        device=settings.bge_device,
        batch_size=settings.bge_batch_size,
        local_files_only=settings.bge_local_files_only,
    )
