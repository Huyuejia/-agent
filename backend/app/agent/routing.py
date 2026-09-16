"""Conservative Workflow/Agent execution boundary."""

import re

from app.agent.domain import ExecutionDecision, ExecutionMode


_EXPLICIT_IDENTIFIER = re.compile(
    r"(?:E\d{3,}|ORD[-:]?\d{4,}|SN[:：-]?[A-Z0-9]{6,})",
    re.IGNORECASE,
)
_DEPENDENT_TRANSITION = re.compile(
    r"(?:先.+(?:再|然后)|查出.+(?:对应|涉及).+(?:再|然后)|"
    r"(?:这个|该|对应的|涉及的)(?:产品|商品|设备|订单))"
)


class ExecutionRouter:
    """Select Agent only when the request explicitly describes a dependency."""

    def decide(self, message: str) -> ExecutionDecision:
        if _EXPLICIT_IDENTIFIER.search(message) and _DEPENDENT_TRANSITION.search(message):
            return ExecutionDecision(
                mode=ExecutionMode.AGENT,
                reason_code="observation_dependent_reference",
            )
        return ExecutionDecision(
            mode=ExecutionMode.WORKFLOW,
            reason_code="deterministic_default",
        )
