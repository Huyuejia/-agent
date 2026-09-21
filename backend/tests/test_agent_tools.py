"""Read-only business tool allow-list tests."""

from app.agent.domain import ToolStatus
from app.agent.factory import _KnowledgeSearchAdapter
from app.agent.tools import AgentToolAdapter
from app.retrieval.domain import Citation, Evidence, RetrievalMode
from app.retrieval.executor import ExecutionStatus, RetrievalExecutionResult
from app.retrieval.exact_repository import ResolvedEntity


class FakeExactRepository:
    def resolve(self, entity_types, entity_values):
        if entity_types == ["error_code"] and entity_values == ["E1001"]:
            return [
                ResolvedEntity(
                    entity_type="error_code",
                    normalized_value="E1001",
                    record_id="error-1",
                    display_identifier="E1001",
                    table="error_codes",
                    attributes={
                        "message": "摄像头离线",
                        "resolution": "检查 Wi-Fi 并重启设备",
                        "product_sku": "Cam-A1",
                    },
                )
            ]
        return []


class FakeGraphService:
    def get_warranty(self, product_name):
        assert product_name == "Cam-A1"
        return {
            "policy_name": "标准保修",
            "duration": "1 年",
            "description": "非人为损坏免费维修。",
        }

    def get_protocols(self, product_name):
        return ["WiFi", "Zigbee"]

    def check_compatibility(self, product_a, product_b):
        return product_a == "Cam-A1" and product_b == "Hub-Z1"


class FakeKnowledgeSearch:
    def __init__(self, evidence=None):
        self.evidence = evidence or []

    def search(self, query, top_k=3):
        return self.evidence[:top_k]


def _knowledge_evidence(evidence_id="document_chunks:chunk-1"):
    return Evidence(
        evidence_id=evidence_id,
        kind=RetrievalMode.HYBRID,
        text="七日内可退货。",
        citation=Citation(
            source_type="document_chunks",
            payload={"document_name": "政策.pdf", "location": "第 1 页"},
        ),
        fused_score=0.42,
    )


def test_second_tool_argument_can_come_from_first_observation():
    tools = AgentToolAdapter(
        exact_repository=FakeExactRepository(),
        graph_service=FakeGraphService(),
        knowledge_search=FakeKnowledgeSearch(),
    )

    first = tools.execute(
        "exact_lookup", {"entity_type": "error_code", "identifier": "E1001"}
    )
    observed_sku = first.data["entities"][0]["attributes"]["product_sku"]
    second = tools.execute(
        "graph_lookup", {"operation": "warranty", "product_a": observed_sku}
    )

    assert first.status is ToolStatus.OK
    assert second.status is ToolStatus.OK
    assert second.data["product"] == "Cam-A1"
    assert second.evidence[0].evidence_id == "neo4j:warranty:Cam-A1"


def test_unknown_or_write_capability_is_not_exposed():
    tools = AgentToolAdapter(
        exact_repository=FakeExactRepository(),
        graph_service=FakeGraphService(),
        knowledge_search=FakeKnowledgeSearch(),
    )

    result = tools.execute("execute_sql", {"sql": "DELETE FROM products"})

    assert result.status is ToolStatus.ERROR
    assert result.error_code == "TOOL_NOT_ALLOWED"
    assert result.retryable is False


def test_tool_arguments_are_validated_at_application_boundary():
    tools = AgentToolAdapter(
        exact_repository=FakeExactRepository(),
        graph_service=FakeGraphService(),
        knowledge_search=FakeKnowledgeSearch(),
    )

    result = tools.execute(
        "graph_lookup",
        {"operation": "warranty", "product_a": "Cam-A1'; MATCH (n) DETACH DELETE n"},
    )

    assert result.status is ToolStatus.ERROR
    assert result.error_code == "INVALID_TOOL_ARGUMENTS"


def test_knowledge_search_preserves_shared_evidence_identity_and_top_k():
    first = _knowledge_evidence("document_chunks:chunk-1")
    second = _knowledge_evidence("document_chunks:chunk-2")
    tools = AgentToolAdapter(
        exact_repository=FakeExactRepository(),
        graph_service=FakeGraphService(),
        knowledge_search=FakeKnowledgeSearch([first, second]),
    )

    result = tools.execute(
        "knowledge_search",
        {"query": "退货政策", "top_k": 1},
    )

    assert result.status is ToolStatus.OK
    assert len(result.evidence) == 1
    assert result.evidence[0].evidence_id == first.evidence_id
    assert result.evidence[0].kind is first.kind
    assert result.evidence[0].text == first.text
    assert result.evidence[0].citation == first.citation


class RetrievalOnlyService:
    def __init__(self, evidence):
        self.evidence = evidence

    def retrieve(self, query):
        return RetrievalExecutionResult(
            status=ExecutionStatus.COMPLETED,
            evidence=self.evidence,
        )

    def answer(self, query):
        raise AssertionError("Knowledge Tool must not call the complete answer path")


def test_knowledge_adapter_consumes_query_result_without_answer_generation():
    evidence = _knowledge_evidence("document_chunks:shared-1")
    adapter = _KnowledgeSearchAdapter(RetrievalOnlyService([evidence]))

    result = adapter.search("退货政策", top_k=1)

    assert result == [evidence]
