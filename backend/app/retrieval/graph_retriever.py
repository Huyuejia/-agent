"""Neo4j GraphRetriever：只消费 EXACT 已解析的稳定商品标识。"""

from __future__ import annotations

import re
from typing import Protocol

from app.retrieval.domain import Citation, Evidence, RetrievalMode, RetrievalStep

_WARRANTY_RE = re.compile(r"保修|延保|质保")
_PROTOCOL_RE = re.compile(r"协议|WiFi|Zigbee|蓝牙|Bluetooth|Thread", re.IGNORECASE)
_COMPATIBILITY_RE = re.compile(r"兼容|搭配|配对|配合|一起用|联动|能不能接|接到|连到")


class GraphQueryService(Protocol):
    def check_compatibility(self, product_a: str, product_b: str) -> bool: ...

    def get_compatible_products(self, product_name: str) -> list[str]: ...

    def get_protocols(self, product_name: str) -> list[str]: ...

    def get_warranty(self, product_name: str) -> dict | None: ...


class GraphRetriever:
    def __init__(self, service: GraphQueryService) -> None:
        self._service = service

    def retrieve(self, query: str, step: RetrievalStep) -> list[Evidence]:
        if step.mode is not RetrievalMode.GRAPH:
            raise ValueError(f"GraphRetriever 只接受 mode=GRAPH，收到 {step.mode}")

        entities = step.filters.get("resolved_entities", [])
        products = self._resolved_products(entities)
        if not products:
            raise ValueError("GRAPH 步骤缺少 EXACT 解析后的商品实体")

        planned_query = step.query or query
        if _WARRANTY_RE.search(planned_query):
            return self._warranties(products)
        if _PROTOCOL_RE.search(planned_query):
            return self._protocols(products)
        if _COMPATIBILITY_RE.search(planned_query):
            return self._compatibility(products)
        raise ValueError("不支持的图谱查询类型")

    @staticmethod
    def _resolved_products(entities: list[dict]) -> list[tuple[str, str]]:
        products: list[tuple[str, str]] = []
        seen: set[str] = set()
        for entity in entities:
            if entity.get("table") != "products":
                continue
            identifier = entity.get("identifier")
            record_id = entity.get("record_id")
            if isinstance(identifier, str) and identifier and identifier not in seen:
                seen.add(identifier)
                products.append((identifier, str(record_id or "")))
        return products

    def _compatibility(self, products: list[tuple[str, str]]) -> list[Evidence]:
        if len(products) == 1:
            product, record_id = products[0]
            compatible = self._service.get_compatible_products(product)
            if not compatible:
                return []
            return [
                self._evidence(
                    evidence_id=f"neo4j:compatible-products:{product}",
                    text=f"{product} 兼容：{', '.join(compatible)}",
                    query_type="compatible_products",
                    products=[product, *compatible],
                    entity_ids=[record_id] if record_id else [],
                    payload={"compatible_products": compatible},
                )
            ]

        if len(products) != 2:
            raise ValueError("兼容性比较需要一个或两个已解析商品")
        (product_a, id_a), (product_b, id_b) = products
        compatible = self._service.check_compatibility(product_a, product_b)
        pair = sorted([product_a, product_b])
        return [
            self._evidence(
                evidence_id=f"neo4j:compatibility:{pair[0]}:{pair[1]}",
                text=f"{product_a} 与 {product_b}{'兼容' if compatible else '不兼容'}",
                query_type="compatibility",
                products=[product_a, product_b],
                entity_ids=[item for item in (id_a, id_b) if item],
                payload={"compatible": compatible},
            )
        ]

    def _protocols(self, products: list[tuple[str, str]]) -> list[Evidence]:
        evidence: list[Evidence] = []
        for product, record_id in products:
            protocols = self._service.get_protocols(product)
            if protocols:
                evidence.append(
                    self._evidence(
                        evidence_id=f"neo4j:protocols:{product}",
                        text=f"{product} 支持协议：{', '.join(protocols)}",
                        query_type="protocols",
                        products=[product],
                        entity_ids=[record_id] if record_id else [],
                        payload={"protocols": protocols},
                    )
                )
        return evidence

    def _warranties(self, products: list[tuple[str, str]]) -> list[Evidence]:
        evidence: list[Evidence] = []
        for product, record_id in products:
            warranty = self._service.get_warranty(product)
            if warranty:
                evidence.append(
                    self._evidence(
                        evidence_id=f"neo4j:warranty:{product}",
                        text=(
                            f"{product} 保修政策：{warranty['policy_name']}，"
                            f"期限 {warranty['duration']}。{warranty['description']}"
                        ),
                        query_type="warranty",
                        products=[product],
                        entity_ids=[record_id] if record_id else [],
                        payload={"warranty": warranty},
                    )
                )
        return evidence

    @staticmethod
    def _evidence(
        *,
        evidence_id: str,
        text: str,
        query_type: str,
        products: list[str],
        entity_ids: list[str],
        payload: dict,
    ) -> Evidence:
        return Evidence(
            evidence_id=evidence_id,
            kind=RetrievalMode.GRAPH,
            text=text,
            citation=Citation(
                source_type="neo4j",
                payload={
                    "query_type": query_type,
                    "products": products,
                    **payload,
                },
            ),
            entity_ids=entity_ids,
            metadata={"query_type": query_type},
        )
