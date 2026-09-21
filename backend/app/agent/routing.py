"""Conservative Workflow/Agent execution boundary."""

import re

from app.agent.domain import ExecutionDecision, ExecutionMode
from app.retrieval.query_analyzer import QueryAnalyzer


_EXPLICIT_IDENTIFIER = re.compile(
    r"(?:E\d{3,}|ORD[-:]?\d{4,}|SN[:：-]?[A-Z0-9]{6,})",
    re.IGNORECASE,
)
_DEPENDENT_TRANSITION = re.compile(
    r"(?:先.+(?:再|然后)|查出.+(?:对应|涉及).+(?:再|然后)|"
    r"(?:这个|该|对应的|涉及的)(?:产品|商品|设备|订单))"
)

_SUPPORTED_MISSING_FIELD_INTENTS = {"graph_query", "error_lookup"}


class ExecutionRouter:
    """Select Agent for dynamic dependencies or supported tasks needing fields."""

    def __init__(self, query_analyzer: QueryAnalyzer | None = None) -> None:
        self._query_analyzer = query_analyzer or QueryAnalyzer()

    def decide(self, message: str) -> ExecutionDecision:
        if _EXPLICIT_IDENTIFIER.search(message) and _DEPENDENT_TRANSITION.search(message):
            return ExecutionDecision(
                mode=ExecutionMode.AGENT,
                reason_code="observation_dependent_reference",
            )
        plan = self._query_analyzer.analyze(message)
        if (
            plan.intent in _SUPPORTED_MISSING_FIELD_INTENTS
            and (plan.requires_clarification or plan.requires_context)
        ):
            return ExecutionDecision(
                mode=ExecutionMode.AGENT,
                reason_code="missing_required_fields",
            )
        return ExecutionDecision(
            mode=ExecutionMode.WORKFLOW,
            reason_code="deterministic_default",
        )
