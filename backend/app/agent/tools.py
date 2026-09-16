"""Validated adapters from Agent tool calls to existing read-only services."""

from __future__ import annotations

import re
from time import perf_counter
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.domain import ToolResult, ToolStatus
from app.retrieval.domain import Citation, Evidence, RetrievalMode, RetrievalStep
from app.retrieval.exact_repository import ExactRepository
from app.retrieval.exact_retriever import ExactRetriever
from app.services.graph_service import PRODUCT_WHITELIST


class GraphQueryPort(Protocol):
    def check_compatibility(self, product_a: str, product_b: str) -> bool: ...
    def get_protocols(self, product_name: str) -> list[str]: ...
    def get_warranty(self, product_name: str) -> dict | None: ...


class KnowledgeSearchPort(Protocol):
    def search(self, query: str, top_k: int = 3) -> dict: ...


class _StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExactLookupArgs(_StrictArgs):
    entity_type: Literal["sku", "order", "serial", "error_code"]
    identifier: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9:：-]+$")


class GraphLookupArgs(_StrictArgs):
    operation: Literal["warranty", "protocols", "compatibility"]
    product_a: str
    product_b: str | None = None


class KnowledgeSearchArgs(_StrictArgs):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=3, ge=1, le=5)


class AgentToolAdapter:
    """Fixed allow-list; never exposes SQL, Cypher, cache, or writes."""

    tool_schemas: dict[str, dict[str, Any]] = {
        "exact_lookup": ExactLookupArgs.model_json_schema(),
        "graph_lookup": GraphLookupArgs.model_json_schema(),
        "knowledge_search": KnowledgeSearchArgs.model_json_schema(),
    }

    def __init__(
        self,
        *,
        exact_repository: ExactRepository,
        graph_service: GraphQueryPort,
        knowledge_search: KnowledgeSearchPort,
    ) -> None:
        self._exact = ExactRetriever(exact_repository)
        self._graph = graph_service
        self._knowledge = knowledge_search

    def execute(self, tool_name: str, raw_arguments: dict[str, Any]) -> ToolResult:
        started = perf_counter()
        if tool_name not in self.tool_schemas:
            return self._finish(
                started,
                ToolResult(
                    status=ToolStatus.ERROR,
                    error_code="TOOL_NOT_ALLOWED",
                    retryable=False,
                ),
            )
        try:
            if tool_name == "exact_lookup":
                result = self._exact_lookup(ExactLookupArgs.model_validate(raw_arguments))
            elif tool_name == "graph_lookup":
                result = self._graph_lookup(GraphLookupArgs.model_validate(raw_arguments))
            else:
                result = self._knowledge_search(
                    KnowledgeSearchArgs.model_validate(raw_arguments)
                )
        except (ValidationError, ValueError):
            result = ToolResult(
                status=ToolStatus.ERROR,
                error_code="INVALID_TOOL_ARGUMENTS",
                retryable=False,
            )
        except Exception:
            result = ToolResult(
                status=ToolStatus.ERROR,
                error_code="TOOL_EXECUTION_ERROR",
                retryable=True,
            )
        return self._finish(started, result)

    def _exact_lookup(self, args: ExactLookupArgs) -> ToolResult:
        normalized = re.sub(r"[-:：]", "", args.identifier.upper())
        step = RetrievalStep(
            step_id="agent_exact_lookup",
            mode=RetrievalMode.EXACT,
            query=args.identifier,
            filters={
                "entity_types": [args.entity_type],
                "entity_values": [normalized],
            },
            required=True,
        )
        evidence = self._exact.retrieve(args.identifier, step)
        if not evidence:
            return ToolResult(status=ToolStatus.NOT_FOUND, retryable=False)
        entities = [
            {
                "identifier": item.citation.payload.get("identifier"),
                "table": item.citation.payload.get("table"),
                "record_id": item.citation.payload.get("record_id"),
                "attributes": item.metadata,
            }
            for item in evidence
        ]
        return ToolResult(
            status=ToolStatus.OK,
            data={"entities": entities},
            evidence=evidence,
        )

    def _graph_lookup(self, args: GraphLookupArgs) -> ToolResult:
        if args.product_a not in PRODUCT_WHITELIST:
            raise ValueError("unknown product")
        if args.operation == "compatibility":
            if args.product_b not in PRODUCT_WHITELIST:
                raise ValueError("compatibility requires product_b")
            compatible = self._graph.check_compatibility(
                args.product_a, args.product_b
            )
            text = (
                f"{args.product_a} 与 {args.product_b} "
                f"{'兼容' if compatible else '不兼容'}"
            )
            key = f"compatibility:{args.product_a}:{args.product_b}"
            data = {"compatible": compatible}
        elif args.operation == "protocols":
            protocols = self._graph.get_protocols(args.product_a)
            if not protocols:
                return ToolResult(status=ToolStatus.NOT_FOUND, retryable=False)
            text = f"{args.product_a} 支持的协议：{', '.join(protocols)}"
            key = f"protocols:{args.product_a}"
            data = {"product": args.product_a, "protocols": protocols}
        else:
            warranty = self._graph.get_warranty(args.product_a)
            if not warranty:
                return ToolResult(status=ToolStatus.NOT_FOUND, retryable=False)
            text = (
                f"{args.product_a} 适用 {warranty['policy_name']}，"
                f"保修期 {warranty['duration']}。{warranty['description']}"
            )
            key = f"warranty:{args.product_a}"
            data = {"product": args.product_a, "warranty": warranty}
        evidence = Evidence(
            evidence_id=f"neo4j:{key}",
            kind=RetrievalMode.GRAPH,
            text=text,
            citation=Citation(
                source_type="neo4j",
                payload={"operation": args.operation, **data},
            ),
        )
        return ToolResult(status=ToolStatus.OK, data=data, evidence=[evidence])

    def _knowledge_search(self, args: KnowledgeSearchArgs) -> ToolResult:
        response = self._knowledge.search(args.query, top_k=args.top_k)
        evidence = []
        for index, source in enumerate(response.get("sources", []), start=1):
            document = source.get("document_name") or "unknown"
            location = source.get("location") or "unknown"
            evidence.append(
                Evidence(
                    evidence_id=f"document:{document}:{location}:{index}",
                    kind=RetrievalMode.HYBRID,
                    text=source.get("snippet") or "",
                    citation=Citation(
                        source_type="document_chunks",
                        payload={
                            "document_name": document,
                            "location": location,
                        },
                    ),
                    fused_score=0.0,
                )
            )
        return ToolResult(
            status=ToolStatus.OK if evidence else ToolStatus.NOT_FOUND,
            data={"query": args.query},
            evidence=evidence,
        )

    @staticmethod
    def _finish(started: float, result: ToolResult) -> ToolResult:
        result.latency_ms = max(0, round((perf_counter() - started) * 1000))
        return result
