"""检索领域模型：枚举 + 统一 Evidence + 检索计划数据结构。

本模块只定义数据结构，不实现任何检索后端。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class RetrievalMode(str, Enum):
    """检索方式。"""

    EXACT = "exact"       # 数据库等值查询（货号/订单号/序列号/错误码）
    GRAPH = "graph"       # Neo4j 关系与多跳查询
    LEXICAL = "lexical"   # 全文关键词检索（BM25/FTS）
    VECTOR = "vector"     # Embedding 语义检索（pgvector）
    HYBRID = "hybrid"     # 词法 + 向量 RRF 融合后的证据


class MissPolicy(str, Enum):
    """检索未命中时的处理策略。"""

    STOP = "stop"          # 立即停止，不降级（EXACT 默认）
    CONTINUE = "continue"  # 继续执行后续步骤
    FALLBACK = "fallback"  # 进入兜底回答


class FusionStrategy(str, Enum):
    """融合策略（受约束，不使用任意字符串）。"""

    RRF = "rrf"


class Citation(BaseModel):
    """结构化来源，不只字符串。source_type 标识来源类型，payload 携带来源细节。"""

    source_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DetectedEntity(BaseModel):
    """QueryAnalyzer 识别出的显式标识符。

    raw_value 为原文，normalized_value 为确定性标准化后的值；
    数据库查询使用 normalized_value。
    """

    type: str  # "sku" | "order" | "serial" | "error_code"
    raw_value: str
    normalized_value: str


class Evidence(BaseModel):
    """统一证据，LLM 唯一消费对象。"""

    evidence_id: str
    kind: RetrievalMode
    text: str
    citation: Citation
    entity_ids: list[str] = Field(default_factory=list)
    # raw_score 只在同一检索器内部可比，不得跨检索器直接比较
    raw_score: float | None = None
    rank: int | None = None
    # fused_score 只由 Hybrid/RRF 产生
    fused_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("rank")
    @classmethod
    def _rank_positive(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("rank 必须为空或 >= 1")
        return value

    @model_validator(mode="after")
    def _fused_score_only_hybrid(self) -> "Evidence":
        if self.fused_score is not None and self.kind is not RetrievalMode.HYBRID:
            raise ValueError("fused_score 只允许出现在 kind=HYBRID 的 Evidence")
        return self


class RetrievalStep(BaseModel):
    """检索计划中的一个步骤。"""

    step_id: str
    mode: RetrievalMode
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int = 5
    required: bool = False
    miss_policy: MissPolicy = MissPolicy.CONTINUE
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("top_k")
    @classmethod
    def _top_k_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("top_k 必须大于 0")
        return value


class RetrievalPlan(BaseModel):
    """QueryAnalyzer 产出的检索计划。"""

    steps: list[RetrievalStep] = Field(default_factory=list)
    fusion_strategy: FusionStrategy | None = None
    detected_entities: list[DetectedEntity] = Field(default_factory=list)
    intent: str = "unknown"
    confidence: float = 0.0
    requires_context: bool = False
    requires_clarification: bool = False
    clarification_question: str | None = None
    output_top_k: int = 5

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence 必须在 [0, 1]")
        return value

    @field_validator("output_top_k")
    @classmethod
    def _output_top_k_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("output_top_k 必须大于 0")
        return value

    @model_validator(mode="after")
    def _check_state(self) -> "RetrievalPlan":
        if self.requires_clarification and not self.clarification_question:
            raise ValueError(
                "requires_clarification=True 时必须提供 clarification_question"
            )
        if self.requires_context or self.requires_clarification:
            for step in self.steps:
                if step.mode is RetrievalMode.GRAPH:
                    raise ValueError(
                        "requires_context/requires_clarification 计划不得包含 GRAPH 步骤"
                    )
        return self
