"""RetrievalExecutor 的计划执行、依赖与降级契约测试。"""

import pytest

from app.retrieval.domain import (
    Citation,
    Evidence,
    FusionStrategy,
    MissPolicy,
    RetrievalMode,
    RetrievalPlan,
    RetrievalStep,
)
from app.retrieval.executor import (
    ExecutionStatus,
    RetrievalExecutionError,
    RetrievalExecutor,
    StepStatus,
)


class FakeRetriever:
    def __init__(self, outputs=None, error: Exception | None = None):
        self.outputs = list(outputs or [])
        self.error = error
        self.calls = []

    def retrieve(self, query, step):
        self.calls.append((query, step))
        if self.error:
            raise self.error
        return list(self.outputs)


def _evidence(
    evidence_id: str,
    kind: RetrievalMode,
    rank: int = 1,
    entity_ids: list[str] | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        kind=kind,
        text=evidence_id,
        citation=Citation(source_type="test", payload={}),
        rank=rank,
        entity_ids=entity_ids or [],
    )


def _step(
    step_id: str,
    mode: RetrievalMode,
    *,
    miss_policy: MissPolicy = MissPolicy.CONTINUE,
    required: bool = False,
    depends_on: list[str] | None = None,
) -> RetrievalStep:
    return RetrievalStep(
        step_id=step_id,
        mode=mode,
        query="planned query",
        top_k=10,
        required=required,
        miss_policy=miss_policy,
        depends_on=depends_on or [],
    )


def test_document_plan_runs_both_retrievers_and_returns_rrf_evidence():
    lexical = FakeRetriever(
        [
            _evidence("chunk:a", RetrievalMode.LEXICAL, rank=1),
            _evidence("chunk:b", RetrievalMode.LEXICAL, rank=2),
        ]
    )
    vector = FakeRetriever(
        [
            _evidence("chunk:a", RetrievalMode.VECTOR, rank=2),
            _evidence("chunk:c", RetrievalMode.VECTOR, rank=1),
        ]
    )
    plan = RetrievalPlan(
        steps=[
            _step("lexical", RetrievalMode.LEXICAL),
            _step("vector", RetrievalMode.VECTOR),
        ],
        fusion_strategy=FusionStrategy.RRF,
        output_top_k=2,
    )

    result = RetrievalExecutor(
        {RetrievalMode.LEXICAL: lexical, RetrievalMode.VECTOR: vector}
    ).execute("original query", plan)

    assert result.status is ExecutionStatus.COMPLETED
    assert [item.evidence_id for item in result.evidence] == ["chunk:a", "chunk:c"]
    assert all(item.kind is RetrievalMode.HYBRID for item in result.evidence)
    assert lexical.calls[0][0] == "original query"
    assert vector.calls[0][0] == "original query"


def test_stop_miss_prevents_later_steps():
    exact = FakeRetriever([])
    graph = FakeRetriever([_evidence("graph:1", RetrievalMode.GRAPH)])
    plan = RetrievalPlan(
        steps=[
            _step(
                "exact", RetrievalMode.EXACT, miss_policy=MissPolicy.STOP, required=True
            ),
            _step("graph", RetrievalMode.GRAPH),
        ]
    )

    result = RetrievalExecutor(
        {RetrievalMode.EXACT: exact, RetrievalMode.GRAPH: graph}
    ).execute("query", plan)

    assert result.status is ExecutionStatus.STOPPED
    assert result.stopped_at == "exact"
    assert graph.calls == []


def test_continue_miss_allows_independent_later_step():
    lexical = FakeRetriever([])
    vector = FakeRetriever([_evidence("chunk:a", RetrievalMode.VECTOR)])
    plan = RetrievalPlan(
        steps=[
            _step("lexical", RetrievalMode.LEXICAL),
            _step("vector", RetrievalMode.VECTOR),
        ]
    )

    result = RetrievalExecutor(
        {RetrievalMode.LEXICAL: lexical, RetrievalMode.VECTOR: vector}
    ).execute("query", plan)

    assert result.status is ExecutionStatus.COMPLETED
    assert [item.evidence_id for item in result.evidence] == ["chunk:a"]
    assert [item.status for item in result.steps] == [StepStatus.MISS, StepStatus.COMPLETED]


def test_fallback_miss_returns_fallback_status():
    lexical = FakeRetriever([])
    plan = RetrievalPlan(
        steps=[_step("lexical", RetrievalMode.LEXICAL, miss_policy=MissPolicy.FALLBACK)]
    )

    result = RetrievalExecutor({RetrievalMode.LEXICAL: lexical}).execute("query", plan)

    assert result.status is ExecutionStatus.FALLBACK
    assert result.stopped_at == "lexical"


def test_dependencies_are_topologically_ordered_and_entity_ids_are_forwarded():
    exact = FakeRetriever(
        [_evidence("product:row", RetrievalMode.EXACT, entity_ids=["uuid-1"])]
    )
    graph = FakeRetriever([_evidence("graph:1", RetrievalMode.GRAPH)])
    plan = RetrievalPlan(
        steps=[
            _step("graph", RetrievalMode.GRAPH, depends_on=["resolve"]),
            _step("resolve", RetrievalMode.EXACT),
        ]
    )

    result = RetrievalExecutor(
        {RetrievalMode.EXACT: exact, RetrievalMode.GRAPH: graph}
    ).execute("query", plan)

    assert [item.step_id for item in result.steps] == ["resolve", "graph"]
    graph_step = graph.calls[0][1]
    assert graph_step.filters["resolved_entity_ids"] == ["uuid-1"]
    assert graph_step.filters["dependency_evidence_ids"] == ["product:row"]


def test_missing_dependency_result_skips_dependent_step_and_stops_if_required():
    exact = FakeRetriever([])
    graph = FakeRetriever([_evidence("graph:1", RetrievalMode.GRAPH)])
    plan = RetrievalPlan(
        steps=[
            _step("resolve", RetrievalMode.EXACT),
            _step("graph", RetrievalMode.GRAPH, required=True, depends_on=["resolve"]),
        ]
    )

    result = RetrievalExecutor(
        {RetrievalMode.EXACT: exact, RetrievalMode.GRAPH: graph}
    ).execute("query", plan)

    assert result.status is ExecutionStatus.STOPPED
    assert result.steps[-1].status is StepStatus.SKIPPED
    assert graph.calls == []


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        (
            RetrievalPlan(
                steps=[
                    _step("same", RetrievalMode.LEXICAL),
                    _step("same", RetrievalMode.VECTOR),
                ]
            ),
            "重复",
        ),
        (
            RetrievalPlan(
                steps=[_step("graph", RetrievalMode.GRAPH, depends_on=["missing"])]
            ),
            "不存在",
        ),
        (
            RetrievalPlan(
                steps=[
                    _step("a", RetrievalMode.LEXICAL, depends_on=["b"]),
                    _step("b", RetrievalMode.VECTOR, depends_on=["a"]),
                ]
            ),
            "循环",
        ),
    ],
)
def test_invalid_dependency_graph_is_rejected(plan, message):
    with pytest.raises(RetrievalExecutionError, match=message):
        RetrievalExecutor({}).execute("query", plan)


def test_clarification_and_context_plans_short_circuit_without_retrieval():
    retriever = FakeRetriever([_evidence("chunk:a", RetrievalMode.LEXICAL)])
    executor = RetrievalExecutor({RetrievalMode.LEXICAL: retriever})

    clarification = executor.execute(
        "query",
        RetrievalPlan(
            requires_clarification=True,
            clarification_question="请提供型号",
        ),
    )
    context = executor.execute("query", RetrievalPlan(requires_context=True))

    assert clarification.status is ExecutionStatus.NEEDS_CLARIFICATION
    assert clarification.clarification_question == "请提供型号"
    assert context.status is ExecutionStatus.NEEDS_CONTEXT
    assert retriever.calls == []


def test_missing_retriever_and_backend_errors_are_explicit():
    plan = RetrievalPlan(steps=[_step("vector", RetrievalMode.VECTOR)])
    with pytest.raises(RetrievalExecutionError, match="未注册"):
        RetrievalExecutor({}).execute("query", plan)

    broken = FakeRetriever(error=RuntimeError("database unavailable"))
    with pytest.raises(RetrievalExecutionError, match="vector") as exc_info:
        RetrievalExecutor({RetrievalMode.VECTOR: broken}).execute("query", plan)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_non_fused_output_is_deduplicated_and_limited():
    lexical = FakeRetriever(
        [
            _evidence("chunk:a", RetrievalMode.LEXICAL, rank=1),
            _evidence("chunk:a", RetrievalMode.LEXICAL, rank=2),
            _evidence("chunk:b", RetrievalMode.LEXICAL, rank=3),
        ]
    )
    plan = RetrievalPlan(
        steps=[_step("lexical", RetrievalMode.LEXICAL)], output_top_k=1
    )

    result = RetrievalExecutor({RetrievalMode.LEXICAL: lexical}).execute("query", plan)

    assert [item.evidence_id for item in result.evidence] == ["chunk:a"]
