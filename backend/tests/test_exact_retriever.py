"""ExactRetriever 单元测试（使用 fake repository，不连数据库）。"""

from app.retrieval import (
    ExactRetriever,
    MissPolicy,
    ResolvedEntity,
    RetrievalMode,
    RetrievalStep,
)


class FakeExactRepository:
    """内存 fake，按预置记录返回。"""

    def __init__(self, records=None):
        self._records = records or []
        self.calls = []

    def resolve(self, entity_types, entity_values):
        self.calls.append((list(entity_types), list(entity_values)))
        return list(self._records)


def _step(filters):
    return RetrievalStep(
        step_id="exact_lookup",
        mode=RetrievalMode.EXACT,
        query="查询",
        filters=filters,
        required=True,
        miss_policy=MissPolicy.STOP,
    )


def _resolved(
    entity_type="sku",
    normalized_value="CAM-A1",
    record_id="11111111-1111-1111-1111-111111111111",
    display_identifier="Cam-A1",
    table="products",
):
    return ResolvedEntity(entity_type, normalized_value, record_id, display_identifier, table)


def test_retrieves_one_evidence_per_entity():
    repo = FakeExactRepository([_resolved()])
    evidence = ExactRetriever(repo).retrieve(
        "查询", _step({"entity_types": ["sku"], "entity_values": ["CAM-A1"]})
    )
    assert len(evidence) == 1
    assert evidence[0].kind is RetrievalMode.EXACT
    assert evidence[0].entity_ids == ["11111111-1111-1111-1111-111111111111"]


def test_evidence_id_uses_table_and_record_id():
    repo = FakeExactRepository([_resolved()])
    evidence = ExactRetriever(repo).retrieve(
        "查询", _step({"entity_types": ["sku"], "entity_values": ["CAM-A1"]})
    )[0]
    assert evidence.evidence_id == "products:11111111-1111-1111-1111-111111111111"


def test_citation_records_table_id_and_identifier():
    repo = FakeExactRepository([_resolved()])
    evidence = ExactRetriever(repo).retrieve(
        "查询", _step({"entity_types": ["sku"], "entity_values": ["CAM-A1"]})
    )[0]
    citation = evidence.citation
    assert citation.source_type == "products"
    assert citation.payload["table"] == "products"
    assert citation.payload["record_id"] == "11111111-1111-1111-1111-111111111111"
    assert citation.payload["identifier"] == "Cam-A1"


def test_miss_returns_empty_list():
    repo = FakeExactRepository([])  # 无匹配记录
    evidence = ExactRetriever(repo).retrieve(
        "查询", _step({"entity_types": ["sku"], "entity_values": ["NOT-FOUND"]})
    )
    assert evidence == []


def test_passes_entity_types_and_values_to_repository():
    repo = FakeExactRepository([_resolved()])
    ExactRetriever(repo).retrieve(
        "查询",
        _step(
            {
                "entity_types": ["sku", "order"],
                "entity_values": ["CAM-A1", "ORD100001"],
            }
        ),
    )
    types, values = repo.calls[0]
    assert types == ["sku", "order"]
    assert values == ["CAM-A1", "ORD100001"]


def test_implements_retriever_protocol():
    from app.retrieval import Retriever

    assert isinstance(ExactRetriever(FakeExactRepository()), Retriever)
