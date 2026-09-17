"""Application-level verification of untrusted Agent candidates."""

from app.agent.domain import AgentCandidate, TaskState, ToolStatus, VerificationResult


class CandidateVerifier:
    def verify(
        self,
        candidate: AgentCandidate,
        state: TaskState,
    ) -> VerificationResult:
        if not candidate.answer.strip() or not candidate.evidence_refs:
            return VerificationResult(
                accepted=False,
                error_code="INCOMPLETE_CANDIDATE",
            )
        if not set(candidate.evidence_refs).issubset(state.evidence_refs):
            return VerificationResult(
                accepted=False,
                error_code="UNOBSERVED_EVIDENCE",
            )
        unresolved_errors = [
            observation
            for index, observation in enumerate(state.tool_observations)
            if observation.result.status is ToolStatus.ERROR
            and not any(
                later.tool_name == observation.tool_name
                and later.arguments == observation.arguments
                and later.result.status in {ToolStatus.OK, ToolStatus.PARTIAL}
                for later in state.tool_observations[index + 1 :]
            )
        ]
        if unresolved_errors:
            return VerificationResult(
                accepted=False,
                error_code="TOOL_FAILURE_PRESENT",
            )
        return VerificationResult(accepted=True)
