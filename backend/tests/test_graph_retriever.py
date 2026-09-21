"""GraphRetriever 单元测试，不连接 Neo4j。"""

import pytest

from app.retrieval.domain import MissPolicy, RetrievalMode, RetrievalStep
from app.retrieval.graph_retriever import GraphRetriever


class FakeGraphService:
    def __init__(self):
        self.calls = []

    def check_compatibility(self, a, b):
        self.calls.append(("compatibility", a, b))
        return (a, b) == ("Cam-A1", "Hub-Z1")

    def get_compatible_products(self, product):
        self.calls.append(("compatible_products", product))
        return ["Hub-Z1"] if product == "Cam-A1" else []

    def get_protocols(self, product):
        self.calls.append(("protocols", product))
        return ["WiFi", "Zigbee"]

    def get_warranty(self, product):
        self.calls.append(("warranty", product))
        return {
            "policy_name": "Standard-1Y",
            "duration": "1年",
            "description": "非人为损坏免费维修",
        }


def _entity(identifier, record_id="uuid-1", table="products"):
    return {"identifier": identifier, "record_id": record_id, "table": table}


def _step(query, entities, mode=RetrievalMode.GRAPH):
    return RetrievalStep(
        step_id="graph",
        mode=mode,
        query=query,
        filters={"resolved_entities": entities},
        top_k=5,
        required=True,
        miss_policy=MissPolicy.STOP,
    )


def test_checks_two_product_compatibility_and_preserves_false_as_evidence():
    service = FakeGraphService()
    retriever = GraphRetriever(service)

    compatible = retriever.retrieve(
        "ignored malicious text",
        _step("Cam-A1 和 Hub-Z1 兼容吗", [_entity("Cam-A1"), _entity("Hub-Z1", "uuid-2")]),
    )
    incompatible = retriever.retrieve(
        "ignored",
        _step("Cam-A1 和 Light-B1 兼容吗", [_entity("Cam-A1"), _entity("Light-B1", "uuid-3")]),
    )

    assert compatible[0].citation.payload["compatible"] is True
    assert incompatible[0].citation.payload["compatible"] is False
    assert "不兼容" in incompatible[0].text
    assert compatible[0].entity_ids == ["uuid-1", "uuid-2"]


def test_lists_compatible_products_for_one_resolved_product():
    evidence = GraphRetriever(FakeGraphService()).retrieve(
        "query", _step("Cam-A1 能和什么产品搭配", [_entity("Cam-A1")])
    )

    assert evidence[0].citation.payload["compatible_products"] == ["Hub-Z1"]


def test_gets_protocols_and_warranty_from_fixed_service_methods():
    service = FakeGraphService()
    retriever = GraphRetriever(service)

    protocols = retriever.retrieve(
        "query", _step("Cam-A1 支持什么协议", [_entity("Cam-A1")])
    )
    warranty = retriever.retrieve(
        "query", _step("Cam-A1 保修多久", [_entity("Cam-A1")])
    )

    assert protocols[0].citation.payload["protocols"] == ["WiFi", "Zigbee"]
    assert warranty[0].citation.payload["warranty"]["duration"] == "1年"
    assert service.calls == [("protocols", "Cam-A1"), ("warranty", "Cam-A1")]


def test_ignores_non_product_dependencies_and_rejects_missing_products():
    retriever = GraphRetriever(FakeGraphService())
    with pytest.raises(ValueError, match="缺少"):
        retriever.retrieve("query", _step("查协议", [_entity("ORD1", table="orders")]))


def test_rejects_non_graph_mode_unknown_query_and_too_many_products():
    retriever = GraphRetriever(FakeGraphService())
    with pytest.raises(ValueError, match="mode=GRAPH"):
        retriever.retrieve("query", _step("协议", [_entity("Cam-A1")], RetrievalMode.EXACT))
    with pytest.raises(ValueError, match="不支持"):
        retriever.retrieve("query", _step("查询信息", [_entity("Cam-A1")]))
    with pytest.raises(ValueError, match="一个或两个"):
        retriever.retrieve(
            "query",
            _step(
                "这些产品兼容吗",
                [_entity("Cam-A1"), _entity("Hub-Z1"), _entity("Light-B1")],
            ),
        )
