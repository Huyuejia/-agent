"""确定性 QueryAnalyzer：把用户输入解析为 RetrievalPlan。

- 不调用 LLM，不访问任何检索后端
- 仅做明确格式标识符识别与步骤编排
"""

from __future__ import annotations

import re

from app.retrieval.domain import (
    DetectedEntity,
    FusionStrategy,
    MissPolicy,
    RetrievalMode,
    RetrievalPlan,
    RetrievalStep,
)

# ---------------------------------------------------------------------------
# 明确格式标识符（订单号 / 序列号 / 错误码 / 货号）
# ---------------------------------------------------------------------------
_ORDER_RE = re.compile(r"\bORD[-:：]?\d{6,}\b", re.IGNORECASE)
_SERIAL_RE = re.compile(r"\bSN[-:：]?[A-Z0-9]{8,}\b", re.IGNORECASE)
_ERROR_RE = re.compile(r"\b(?:E\d{3,4}|ERR[-:：]?\d{3,4})\b", re.IGNORECASE)
_SKU_RE = re.compile(r"\b[A-Z][a-zA-Z]{1,7}-[A-Z]\d{1,2}\b", re.IGNORECASE)

# 关系 / 图谱类问题关键词
_RELATION_RE = re.compile(
    r"兼容|搭配|配对|配合|能不能一起|一起用|联动|支持什么协议|协议|保修|延保|能不能接|接到|连到"
)

# 明确指代（需要会话上下文才能解析实体）
_ANAPHORA_RE = re.compile(
    r"这两个|这两件|这两款|这俩|那两|那两款|那两件|它们|他们|这些|那些|"
    r"该设备|该商品|该产品|该型号|这个|那个|这款|那款|这件|那件"
)

# 全局唯一标识符类型：命中即 EXACT
_UNIQUE_ID_TYPES = {"order", "serial", "error_code"}


def _normalize_entity(entity_type: str, raw_value: str) -> str:
    """确定性标准化：统一大写；除货号外去除前缀与主体之间的分隔符。"""
    value = raw_value.upper()
    if entity_type != "sku":
        value = re.sub(r"[-:：]", "", value)
    return value


def _detect_entities(text: str) -> list[DetectedEntity]:
    entities: list[DetectedEntity] = []
    for pattern, entity_type in (
        (_ORDER_RE, "order"),
        (_SERIAL_RE, "serial"),
        (_ERROR_RE, "error_code"),
        (_SKU_RE, "sku"),
    ):
        for match in pattern.finditer(text):
            raw = match.group(0)
            entities.append(
                DetectedEntity(
                    type=entity_type,
                    raw_value=raw,
                    normalized_value=_normalize_entity(entity_type, raw),
                )
            )
    return entities


def _is_relation_query(text: str) -> bool:
    return bool(_RELATION_RE.search(text))


def _has_anaphora(text: str) -> bool:
    return bool(_ANAPHORA_RE.search(text))


def _dedupe_entities(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """按 (type, normalized_value) 去重，保持首次出现顺序。"""
    seen: set[tuple[str, str]] = set()
    deduped: list[DetectedEntity] = []
    for entity in entities:
        key = (entity.type, entity.normalized_value)
        if key not in seen:
            seen.add(key)
            deduped.append(entity)
    return deduped


def _entity_filters(entities: list[DetectedEntity]) -> dict:
    # 数据库查询使用标准化值；去重后保持稳定顺序
    deduped = _dedupe_entities(entities)
    return {
        "entity_types": [e.type for e in deduped],
        "entity_values": [e.normalized_value for e in deduped],
    }


def _exact_step(
    step_id: str,
    query: str,
    entities: list[DetectedEntity],
) -> RetrievalStep:
    # top_k 至少等于去重后的显式标识符数量，不得固定为 1
    top_k = max(1, len(_dedupe_entities(entities)))
    return RetrievalStep(
        step_id=step_id,
        mode=RetrievalMode.EXACT,
        query=query,
        filters=_entity_filters(entities),
        top_k=top_k,
        required=True,
        miss_policy=MissPolicy.STOP,
    )


class QueryAnalyzer:
    """确定性地把用户问题解析为 RetrievalPlan。"""

    def analyze(self, query: str) -> RetrievalPlan:
        text = (query or "").strip()
        if not text:
            return RetrievalPlan(
                intent="clarification",
                confidence=0.0,
                requires_clarification=True,
                clarification_question="请描述您要咨询的问题。",
                output_top_k=1,
            )

        entities = _detect_entities(text)
        has_unique_id = any(e.type in _UNIQUE_ID_TYPES for e in entities)
        sku_entities = [e for e in entities if e.type == "sku"]
        relation = _is_relation_query(text)

        # 显式唯一标识符 → EXACT，未命中 STOP，不语义降级
        if has_unique_id:
            return RetrievalPlan(
                steps=[_exact_step("exact_lookup", text, entities)],
                detected_entities=entities,
                intent="exact_lookup",
                confidence=1.0,
                output_top_k=1,
            )

        # 关系问题
        if relation:
            if sku_entities:
                resolve = _exact_step("resolve_entities", text, sku_entities)
                graph = RetrievalStep(
                    step_id="graph_lookup",
                    mode=RetrievalMode.GRAPH,
                    query=text,
                    filters={},  # GRAPH 不携带原始 SKU 作为最终实体 ID
                    top_k=5,
                    required=True,
                    miss_policy=MissPolicy.STOP,
                    depends_on=["resolve_entities"],
                )
                return RetrievalPlan(
                    steps=[resolve, graph],
                    detected_entities=entities,
                    intent="graph_query",
                    confidence=0.9,
                    output_top_k=5,
                )
            if _has_anaphora(text):
                # 只有指代、无实体 → 需要上下文，不生成 GRAPH
                return RetrievalPlan(
                    steps=[],
                    detected_entities=entities,
                    intent="graph_query",
                    confidence=0.5,
                    requires_context=True,
                    output_top_k=5,
                )
            # 无实体且非明确指代 → 澄清
            return RetrievalPlan(
                steps=[],
                detected_entities=entities,
                intent="graph_query",
                confidence=0.3,
                requires_clarification=True,
                clarification_question="请提供需要查询的具体商品名称或型号（如 Cam-A1）。",
                output_top_k=5,
            )

        # 单货号 → EXACT 商品主数据查询
        if sku_entities:
            return RetrievalPlan(
                steps=[_exact_step("exact_lookup", text, sku_entities)],
                detected_entities=entities,
                intent="exact_lookup",
                confidence=1.0,
                output_top_k=1,
            )

        # 文档型自然语言问题 → LEXICAL + VECTOR，融合由执行器用 RRF 完成
        return RetrievalPlan(
            steps=[
                RetrievalStep(
                    step_id="lexical",
                    mode=RetrievalMode.LEXICAL,
                    query=text,
                    top_k=10,
                    required=False,
                    miss_policy=MissPolicy.CONTINUE,
                ),
                RetrievalStep(
                    step_id="vector",
                    mode=RetrievalMode.VECTOR,
                    query=text,
                    top_k=10,
                    required=False,
                    miss_policy=MissPolicy.CONTINUE,
                ),
            ],
            fusion_strategy=FusionStrategy.RRF,
            detected_entities=entities,
            intent="document_qa",
            confidence=0.5,
            output_top_k=5,
        )
