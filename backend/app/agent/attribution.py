"""Deterministic failure attribution for replayable Agent runs."""

from app.agent.domain import FailureCategory, TaskState, ToolStatus


class FailureAttributor:
    """Classify only failures with direct trace/state evidence."""

    _VERIFICATION_CODES = {
        "EVIDENCE_REQUIRED",
        "INCOMPLETE_CANDIDATE",
        "MISSING_INFORMATION",
        "TOOL_FAILURE_PRESENT",
        "UNOBSERVED_EVIDENCE",
        "VERIFICATION_REJECTED",
    }

    _DIRECT_CATEGORIES = {
        "TASK_UNDERSTANDING": FailureCategory.TASK_UNDERSTANDING,
        "NEXT_ACTION": FailureCategory.NEXT_ACTION,
        "STATE_LOSS": FailureCategory.STATE_LOSS,
        "FINAL_ANSWER": FailureCategory.FINAL_ANSWER,
    }

    def attribute(self, state: TaskState, error_code: str) -> FailureCategory:
        if error_code in self._VERIFICATION_CODES:
            return FailureCategory.VERIFICATION
        if error_code in self._DIRECT_CATEGORIES:
            return self._DIRECT_CATEGORIES[error_code]

        unresolved_errors = [
            observation.result.error_code
            for index, observation in enumerate(state.tool_observations)
            if observation.result.status is ToolStatus.ERROR
            and not any(
                later.tool_name == observation.tool_name
                and later.arguments == observation.arguments
                and later.result.status in {ToolStatus.OK, ToolStatus.PARTIAL}
                for later in state.tool_observations[index + 1 :]
            )
        ]
        if "TOOL_NOT_ALLOWED" in unresolved_errors:
            return FailureCategory.WRONG_TOOL
        if "INVALID_TOOL_ARGUMENTS" in unresolved_errors:
            return FailureCategory.WRONG_TOOL_ARGS
        if unresolved_errors:
            return FailureCategory.TOOL_OR_RETRIEVAL
        return FailureCategory.UNATTRIBUTED
