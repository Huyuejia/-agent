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
        if any(
            observation.result.status is ToolStatus.ERROR
            for observation in state.tool_observations
        ):
            return VerificationResult(
                accepted=False,
                error_code="TOOL_FAILURE_PRESENT",
            )
        return VerificationResult(accepted=True)
