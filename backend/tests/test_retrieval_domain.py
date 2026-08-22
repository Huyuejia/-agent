"""检索领域模型单元测试。"""

import pytest
from pydantic import ValidationError

from app.retrieval import (
    Citation,
    DetectedEntity,
    Evidence,
    FusionStrategy,
    MissPolicy,
    RetrievalMode,
    RetrievalPlan,
    RetrievalStep,
)


def test_retrieval_mode_values():
    assert {m.value for m in RetrievalMode} == {
        "exact",
        "graph",
        "lexical",
        "vector",
        "hybrid",
    }


def test_miss_policy_values():
    assert {m.value for m in MissPolicy} == {"stop", "continue", "fallback"}


def test_fusion_strategy_values():
    assert {m.value for m in FusionStrategy} == {"rrf"}


def test_citation_is_structured_not_string():
    citation = Citation(
        source_type="knowledge_graph",
        payload={"relation": "Cam-A1 COMPATIBLE_WITH Hub-Z1"},
    )
    assert citation.source_type == "knowledge_graph"
    assert citation.payload["relation"] == "Cam-A1 COMPATIBLE_WITH Hub-Z1"


def test_detected_entity_has_raw_and_normalized():
    entity = DetectedEntity(
        type="sku", raw_value="cam-a1", normalized_value="CAM-A1"
    )
    assert entity.type == "sku"
    assert entity.raw_value == "cam-a1"
    assert entity.normalized_value == "CAM-A1"


def test_evidence_fields_and_defaults():
    evidence = Evidence(
        evidence_id="e-1",
        kind=RetrievalMode.GRAPH,
        text="Cam-A1 与 Hub-Z1 兼容",
        citation=Citation(source_type="knowledge_graph", payload={}),
    )
    assert evidence.entity_ids == []
    assert evidence.raw_score is None
    assert evidence.rank is None
    assert evidence.fused_score is None
    assert evidence.metadata == {}


def test_evidence_fused_score_only_on_hybrid():
    evidence = Evidence(
        evidence_id="e-2",
        kind=RetrievalMode.HYBRID,
        text="融合结果",
        citation=Citation(source_type="document", payload={}),
        fused_score=0.97,
    )
    assert evidence.fused_score == 0.97


def test_evidence_fused_score_on_non_hybrid_raises():
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="e-3",
            kind=RetrievalMode.VECTOR,
            text="非融合证据",
            citation=Citation(source_type="document", payload={}),
            fused_score=0.9,
        )


def test_evidence_rank_validator():
    assert Evidence(
        evidence_id="e-4",
        kind=RetrievalMode.VECTOR,
        text="x",
        citation=Citation(source_type="document", payload={}),
        rank=1,
    ).rank == 1
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="e-5",
            kind=RetrievalMode.VECTOR,
            text="x",
            citation=Citation(source_type="document", payload={}),
            rank=0,
        )


def test_retrieval_step_defaults():
    step = RetrievalStep(step_id="vector", mode=RetrievalMode.VECTOR, query="退货政策")
    assert step.step_id == "vector"
    assert step.filters == {}
    assert step.top_k == 5
    assert step.required is False
    assert step.miss_policy is MissPolicy.CONTINUE
    assert step.depends_on == []


def test_retrieval_step_depends_on():
    step = RetrievalStep(
        step_id="graph_lookup",
        mode=RetrievalMode.GRAPH,
        query="Cam-A1 兼容吗",
        depends_on=["resolve_entities"],
    )
    assert step.depends_on == ["resolve_entities"]


def test_retrieval_step_top_k_positive():
    with pytest.raises(ValidationError):
        RetrievalStep(step_id="s", mode=RetrievalMode.VECTOR, query="x", top_k=0)


def test_retrieval_plan_construction():
    step = RetrievalStep(
        step_id="exact_lookup",
        mode=RetrievalMode.EXACT,
        query="ORD-123456",
        required=True,
        miss_policy=MissPolicy.STOP,
    )
    plan = RetrievalPlan(
        steps=[step],
        intent="exact_lookup",
        confidence=1.0,
        detected_entities=[
            DetectedEntity(type="order", raw_value="ORD-123456", normalized_value="ORD123456")
        ],
    )
    assert plan.steps == [step]
    assert plan.fusion_strategy is None
    assert plan.detected_entities[0].type == "order"
    assert plan.detected_entities[0].normalized_value == "ORD123456"


def test_retrieval_plan_confidence_range():
    with pytest.raises(ValidationError):
        RetrievalPlan(confidence=1.5)
    with pytest.raises(ValidationError):
        RetrievalPlan(confidence=-0.1)


def test_retrieval_plan_output_top_k_positive():
    with pytest.raises(ValidationError):
        RetrievalPlan(output_top_k=0)


def test_retrieval_plan_requires_clarification_needs_question():
    with pytest.raises(ValidationError):
        RetrievalPlan(requires_clarification=True, clarification_question=None)


def test_retrieval_plan_requires_context_must_not_contain_graph():
    graph_step = RetrievalStep(
        step_id="graph_lookup", mode=RetrievalMode.GRAPH, query="兼容吗"
    )
    with pytest.raises(ValidationError):
        RetrievalPlan(steps=[graph_step], requires_context=True)


def test_retriever_protocol_is_structural():
    from app.retrieval import Retriever

    class FakeRetriever:
        def retrieve(self, query, step):
            return [
                Evidence(
                    evidence_id="e-6",
                    kind=RetrievalMode.VECTOR,
                    text="x",
                    citation=Citation(source_type="document", payload={}),
                )
            ]

    assert isinstance(FakeRetriever(), Retriever)
