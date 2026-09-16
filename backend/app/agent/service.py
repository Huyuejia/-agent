"""Application-owned Agent task lifecycle, verification, and persistence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session

from app.agent.domain import (
    RuntimeResult,
    TaskState,
    TaskStatus,
    ToolResult,
    ToolStatus,
)
from app.agent.tools import AgentToolAdapter
from app.agent.verification import CandidateVerifier
from app.models.agent_run import AgentRun, AgentTraceEvent
from app.schemas.conversation import MessageSource


class PiRuntimePort(Protocol):
    runtime_version: str

    def run(
        self,
        *,
        objective: str,
        task_state: TaskState,
        tool_schemas: dict[str, dict[str, Any]],
        execute_tool: Callable[[str, dict[str, Any]], ToolResult],
        emit_event: Callable[..., None],
    ) -> RuntimeResult: ...


class AgentTaskService:
    def __init__(
        self,
        *,
        runtime: PiRuntimePort,
        tool_adapter_factory: Callable[[Session], AgentToolAdapter],
        verifier: CandidateVerifier | None = None,
        max_tool_calls: int = 6,
    ) -> None:
        self._runtime = runtime
        self._tool_adapter_factory = tool_adapter_factory
        self._verifier = verifier or CandidateVerifier()
        self._max_tool_calls = max_tool_calls

    def execute(
        self,
        *,
        objective: str,
        conversation_id: int,
        user_id: int,
        request_id: str,
        boundary_reason: str,
        db: Session,
    ) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        state = TaskState(
            task_id=run_id,
            conversation_id=conversation_id,
            objective=objective,
        )
        run = AgentRun(
            id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            request_id=request_id or uuid.uuid4().hex,
            objective=objective,
            execution_mode="agent",
            status=state.status.value,
            task_state=state.model_dump(mode="json"),
            runtime_version=self._runtime.runtime_version,
        )
        db.add(run)
        sequence = 0

        def trace(
            event_type: str,
            payload: dict[str, Any] | None = None,
            *,
            latency_ms: int | None = None,
            error_code: str | None = None,
        ) -> None:
            nonlocal sequence
            sequence += 1
            db.add(
                AgentTraceEvent(
                    run_id=run_id,
                    sequence_number=sequence,
                    event_type=event_type,
                    payload=payload or {},
                    latency_ms=latency_ms,
                    error_code=error_code,
                )
            )

        trace("goal_received", {"objective": objective})
        trace(
            "boundary_decision",
            {"mode": "agent", "reason_code": boundary_reason},
        )
        tools = self._tool_adapter_factory(db)
        tool_call_count = 0

        def execute_tool(tool_name: str, arguments: dict[str, Any]) -> ToolResult:
            nonlocal tool_call_count
            tool_call_count += 1
            trace("tool_call", {"tool_name": tool_name, "arguments": arguments})
            if tool_call_count > self._max_tool_calls:
                result = ToolResult(
                    status=ToolStatus.ERROR,
                    error_code="TOOL_CALL_LIMIT_EXCEEDED",
                    retryable=False,
                )
            else:
                result = tools.execute(tool_name, arguments)
            state.observe(tool_name, arguments, result)
            run.task_state = state.model_dump(mode="json")
            trace(
                "tool_observation",
                {
                    "tool_name": tool_name,
                    "status": result.status.value,
                    "data": result.data,
                    "evidence_refs": [
                        evidence.evidence_id for evidence in result.evidence
                    ],
                    "retryable": result.retryable,
                },
                latency_ms=result.latency_ms,
                error_code=result.error_code,
            )
            return result

        try:
            runtime_result = self._runtime.run(
                objective=objective,
                task_state=state,
                tool_schemas=tools.tool_schemas,
                execute_tool=execute_tool,
                emit_event=trace,
            )
            for event in runtime_result.events:
                trace(
                    event.event_type,
                    event.payload,
                    latency_ms=event.latency_ms,
                    error_code=event.error_code,
                )
            if runtime_result.candidate is None:
                return self._fail(
                    db,
                    run,
                    state,
                    trace,
                    runtime_result.error_code or "RUNTIME_NO_CANDIDATE",
                )
            state.transition(TaskStatus.VERIFYING)
            trace(
                "state_transition",
                {"from": "RUNNING", "to": "VERIFYING"},
            )
            verification = self._verifier.verify(runtime_result.candidate, state)
            if not verification.accepted:
                return self._fail(
                    db,
                    run,
                    state,
                    trace,
                    verification.error_code or "VERIFICATION_REJECTED",
                )
            state.transition(TaskStatus.SUCCEEDED, verified=True)
            candidate = runtime_result.candidate
            run.status = state.status.value
            run.task_state = state.model_dump(mode="json")
            run.final_answer = candidate.answer
            run.completed_at = datetime.now(timezone.utc)
            trace(
                "state_transition",
                {"from": "VERIFYING", "to": "SUCCEEDED"},
            )
            trace(
                "verification_accepted",
                {"evidence_refs": candidate.evidence_refs},
            )
            db.commit()
            return {
                "answer": candidate.answer,
                "intent": candidate.intent,
                "confidence": candidate.confidence,
                "source_type": candidate.source_type,
                "sources": self._message_sources(state, candidate.evidence_refs),
                "handoff_required": candidate.handoff_required,
                "execution_mode": "agent",
                "agent_run_id": run_id,
                "task_status": state.status.value,
            }
        except Exception:
            return self._fail(
                db,
                run,
                state,
                trace,
                "AGENT_RUNTIME_ERROR",
            )

    @staticmethod
    def _fail(db, run, state, trace, error_code: str) -> dict[str, Any]:
        previous = state.status.value
        state.transition(TaskStatus.FAILED)
        run.status = state.status.value
        run.task_state = state.model_dump(mode="json")
        run.failure_category = error_code
        run.completed_at = datetime.now(timezone.utc)
        trace(
            "verification_rejected",
            {"from": previous, "to": "FAILED"},
            error_code=error_code,
        )
        db.commit()
        return {
            "answer": (
                "Agent 未能生成通过证据校验的结果。"
                "为避免给出未经支持的答案，请稍后重试或联系人工客服。"
            ),
            "intent": "agent_error",
            "confidence": 0.0,
            "source_type": "fallback",
            "sources": [],
            "handoff_required": True,
            "execution_mode": "agent",
            "agent_run_id": run.id,
            "task_status": state.status.value,
        }

    @staticmethod
    def _message_sources(
        state: TaskState,
        evidence_refs: list[str],
    ) -> list[MessageSource]:
        allowed = set(evidence_refs)
        sources: list[MessageSource] = []
        for observation in state.tool_observations:
            for evidence in observation.result.evidence:
                if evidence.evidence_id not in allowed:
                    continue
                payload = evidence.citation.payload
                if evidence.kind.value == "graph":
                    sources.append(
                        MessageSource(
                            source_type="knowledge_graph",
                            relation=evidence.text,
                        )
                    )
                elif evidence.kind.value in {"lexical", "vector", "hybrid"}:
                    sources.append(
                        MessageSource(
                            source_type="document_rag",
                            document_name=payload.get("document_name"),
                            location=payload.get("location"),
                            snippet=evidence.text,
                        )
                    )
                else:
                    sources.append(
                        MessageSource(source_type="exact", relation=evidence.text)
                    )
        return sources
