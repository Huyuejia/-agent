"""Application-owned Agent task lifecycle, verification, and persistence."""

from __future__ import annotations

import uuid
import json
import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session
from sqlalchemy import func, select

from app.agent.domain import (
    RuntimeResult,
    TaskState,
    TaskStatus,
    ToolResult,
    ToolStatus,
)
from app.agent.tools import AgentToolAdapter
from app.agent.attribution import FailureAttributor
from app.agent.verification import CandidateVerifier
from app.models.agent_run import AgentRun, AgentTraceEvent
from app.models.conversation import Conversation
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
        max_tool_retries: int = 1,
        max_tool_calls: int = 6,
        max_verification_repairs: int = 1,
    ) -> None:
        self._runtime = runtime
        self._tool_adapter_factory = tool_adapter_factory
        self._max_tool_retries = max_tool_retries
        self._verifier = verifier or CandidateVerifier()
        self._max_tool_calls = max_tool_calls
        self._max_verification_repairs = max_verification_repairs

    def waiting_run_id(self, *, conversation_id: int, user_id: int, db: Session) -> str | None:
        return db.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.user_id == user_id,
                AgentRun.status == TaskStatus.WAITING_FOR_USER.value,
            )
            .order_by(AgentRun.started_at.desc())
        )

    def active_run_id(self, *, conversation_id: int, user_id: int, db: Session) -> str | None:
        return db.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.user_id == user_id,
                AgentRun.status.in_(
                    [
                        TaskStatus.RUNNING.value,
                        TaskStatus.WAITING_FOR_USER.value,
                        TaskStatus.VERIFYING.value,
                    ]
                ),
            )
            .order_by(AgentRun.started_at.desc())
        )

    @staticmethod
    def _lock_conversation(*, conversation_id: int, user_id: int, db: Session) -> None:
        conversation = db.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .with_for_update()
        )
        if conversation is None:
            raise ValueError("conversation does not exist")


    def execute(
        self,
        *,
        objective: str,
        conversation_id: int,
        user_id: int,
        request_id: str,
        boundary_reason: str,
        db: Session,
        resume_run_id: str | None = None,
        resume_fields: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self._lock_conversation(conversation_id=conversation_id, user_id=user_id, db=db)
        if resume_run_id:
            run = db.scalar(
                select(AgentRun).where(
                    AgentRun.id == resume_run_id,
                    AgentRun.conversation_id == conversation_id,
                    AgentRun.user_id == user_id,
                    AgentRun.status == TaskStatus.WAITING_FOR_USER.value,
                )
            )
            if run is None:
                raise ValueError("resumable agent task does not exist")
            state = TaskState.model_validate(run.task_state)
            previous_status = state.status.value
            state.resume_with_user_message(objective, resume_fields)
            objective = state.objective
            run.request_id = request_id or uuid.uuid4().hex
            run.status = state.status.value
            run.task_state = state.model_dump(mode="json")
            run.completed_at = None
            run_id = run.id
            sequence = db.scalar(
                select(func.max(AgentTraceEvent.sequence_number)).where(
                    AgentTraceEvent.run_id == run_id
                )
            ) or 0
        else:
            active_run_id = self.active_run_id(
                conversation_id=conversation_id, user_id=user_id, db=db
            )
            if active_run_id:
                raise ValueError("active agent task must be resumed or completed")
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
            previous_status = None

        def trace(
            event_type: str,
            payload: dict[str, Any] | None = None,
            *,
            latency_ms: int | None = None,
            error_code: str | None = None,
        ) -> None:
            nonlocal sequence
            sequence += 1
            event_payload = {
                "run_id": run_id,
                "request_id": run.request_id,
                **(payload or {}),
            }
            db.add(
                AgentTraceEvent(
                    run_id=run_id,
                    sequence_number=sequence,
                    event_type=event_type,
                    payload=event_payload,
                    latency_ms=latency_ms,
                    error_code=error_code,
                )
            )

        tools = self._tool_adapter_factory(db)
        if previous_status is None:
            trace(
                "RUN_STARTED",
                {
                    "objective": objective,
                    "task_status_after": state.status.value,
                    "runtime_version": self._runtime.runtime_version,
                    "model_identifier": getattr(self._runtime, "model_identifier", None),
                    "prompt_version": getattr(self._runtime, "prompt_version", None),
                    "tool_schema_version": self._tool_schema_version(tools.tool_schemas),
                },
            )
        else:
            trace(
                "STATE_TRANSITION",
                {
                    "from": previous_status,
                    "to": state.status.value,
                    "task_status_before": previous_status,
                    "task_status_after": state.status.value,
                },
            )
        trace(
            "BOUNDARY_DECISION",
            {"mode": "agent", "reason_code": boundary_reason},
        )
        tool_call_count = 0

        def record_observation(
            tool_name: str, arguments: dict[str, Any], result: ToolResult
        ) -> None:
            state.observe(tool_name, arguments, result)
            run.task_state = state.model_dump(mode="json")
            trace(
                "TOOL_RESULT",
                {
                    "tool_name": tool_name,
                    "status": result.status.value,
                    "observation_summary": result.data,
                    "evidence_refs": [
                        evidence.evidence_id for evidence in result.evidence
                    ],
                    "retryable": result.retryable,
                },
                latency_ms=result.latency_ms,
                error_code=result.error_code,
            )

        def execute_tool(tool_name: str, arguments: dict[str, Any]) -> ToolResult:
            nonlocal tool_call_count
            retry_key = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"
            retries = 0
            while True:
                if tool_call_count >= self._max_tool_calls:
                    limit_result = ToolResult(
                        status=ToolStatus.ERROR,
                        error_code="TOOL_CALL_LIMIT_EXCEEDED",
                        retryable=False,
                    )
                    record_observation(tool_name, arguments, limit_result)
                    return limit_result
                tool_call_count += 1
                trace(
                    "TOOL_CALL",
                    {"tool_name": tool_name, "arguments": arguments, "attempt": retries + 1},
                )
                result = tools.execute(tool_name, arguments)
                record_observation(tool_name, arguments, result)
                if (
                    result.status is not ToolStatus.ERROR
                    or not result.retryable
                    or retries >= self._max_tool_retries
                ):
                    return result
                retries += 1
                state.retry_info[retry_key] = retries
                run.task_state = state.model_dump(mode="json")
                trace(
                    "RETRY",
                    {"tool_name": tool_name, "arguments": arguments, "attempt": retries + 1},
                    error_code=result.error_code,
                )

        ask_user_traced = False

        def emit_runtime_event(
            event_type: str,
            payload: dict[str, Any] | None = None,
            *,
            latency_ms: int | None = None,
            error_code: str | None = None,
        ) -> None:
            nonlocal ask_user_traced
            for decision in self._model_decisions(event_type, payload or {}):
                if decision.get("decision") == "ASK_USER":
                    ask_user_traced = True
                trace(
                    "MODEL_DECISION",
                    decision,
                    latency_ms=latency_ms,
                    error_code=error_code,
                )

        def trace_runtime_events(runtime_result: RuntimeResult) -> None:
            for event in runtime_result.events:
                emit_runtime_event(
                    event.event_type,
                    event.payload,
                    latency_ms=event.latency_ms,
                    error_code=event.error_code,
                )

        def trace_verification_result(candidate, verification) -> None:
            trace(
                "VERIFICATION_RESULT",
                {
                    "accepted": verification.accepted,
                    "violations": verification.violations,
                    "evidence_refs": candidate.evidence_refs,
                },
                error_code=verification.error_code,
            )

        try:
            runtime_result = self._runtime.run(
                objective=objective,
                task_state=state,
                tool_schemas=tools.tool_schemas,
                execute_tool=execute_tool,
                emit_event=emit_runtime_event,
            )
            trace_runtime_events(runtime_result)
            if runtime_result.clarification_text:
                previous = state.status.value
                if not ask_user_traced:
                    trace(
                        "MODEL_DECISION",
                        {
                            "decision": "ASK_USER",
                            "requested_fields": runtime_result.requested_fields,
                        },
                    )
                state.wait_for_user(
                    runtime_result.clarification_text,
                    runtime_result.requested_fields,
                )
                run.status = state.status.value
                run.task_state = state.model_dump(mode="json")
                trace(
                    "STATE_TRANSITION",
                    {
                        "from": previous,
                        "to": TaskStatus.WAITING_FOR_USER.value,
                        "requested_fields": runtime_result.requested_fields,
                    },
                )
                db.commit()
                return {
                    "answer": runtime_result.clarification_text,
                    "intent": "agent_needs_input",
                    "confidence": 1.0,
                    "source_type": "agent",
                    "sources": [],
                    "handoff_required": False,
                    "execution_mode": "agent",
                    "agent_run_id": run_id,
                    "task_status": state.status.value,
                    "needs_user_input": True,
                    "requested_fields": runtime_result.requested_fields,
                }
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
                "STATE_TRANSITION",
                {"from": "RUNNING", "to": "VERIFYING"},
            )
            verification = self._verifier.verify(runtime_result.candidate, state)
            trace_verification_result(runtime_result.candidate, verification)
            repair_attempt = 0
            while not verification.accepted:
                repair = getattr(self._runtime, "repair", None)
                if (
                    repair_attempt >= self._max_verification_repairs
                    or not callable(repair)
                ):
                    return self._fail(
                        db,
                        run,
                        state,
                        trace,
                        verification.error_code or "VERIFICATION_REJECTED",
                    )
                repair_attempt += 1
                trace(
                    "RETRY",
                    {
                        "reason": "VERIFICATION_REPAIR",
                        "violations": verification.violations,
                        "attempt": repair_attempt,
                    },
                    error_code=verification.error_code,
                )
                repaired_result = repair(
                    objective=objective,
                    task_state=state,
                    tool_schemas=tools.tool_schemas,
                    execute_tool=execute_tool,
                    emit_event=emit_runtime_event,
                    verification=verification,
                )
                trace_runtime_events(repaired_result)
                if repaired_result.candidate is None:
                    return self._fail(
                        db,
                        run,
                        state,
                        trace,
                        repaired_result.error_code or "RUNTIME_NO_CANDIDATE",
                    )
                runtime_result = repaired_result
                verification = self._verifier.verify(runtime_result.candidate, state)
                trace_verification_result(runtime_result.candidate, verification)
            state.transition(TaskStatus.SUCCEEDED, verified=True)
            candidate = runtime_result.candidate
            run.status = state.status.value
            run.task_state = state.model_dump(mode="json")
            run.final_answer = candidate.answer
            run.completed_at = datetime.now(timezone.utc)
            trace(
                "STATE_TRANSITION",
                {"from": "VERIFYING", "to": "SUCCEEDED"},
            )
            trace(
                "RUN_COMPLETED",
                {"task_status_after": "SUCCEEDED", "evidence_refs": candidate.evidence_refs},
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
        failure_category = FailureAttributor().attribute(state, error_code)
        run.failure_category = failure_category.value
        run.completed_at = datetime.now(timezone.utc)
        trace(
            "STATE_TRANSITION",
            {"from": previous, "to": "FAILED", "task_status_after": "FAILED"},
            error_code=error_code,
        )
        trace(
            "RUN_COMPLETED",
            {"task_status_after": "FAILED", "failure_category": failure_category.value},
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
            "failure_category": failure_category.value,
        }

    @staticmethod
    def _tool_schema_version(tool_schemas: dict[str, dict[str, Any]]) -> str:
        serialized = json.dumps(tool_schemas, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _model_decisions(event_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if event_type == "MODEL_DECISION":
            decision = payload.get("decision")
            if decision in {"CALL_TOOL", "ASK_USER", "FINALIZE"}:
                return [{key: value for key, value in payload.items() if key != "reasoning"}]
            return []
        if event_type == "model_action":
            action = payload.get("action")
            if isinstance(action, str):
                return [{"decision": "CALL_TOOL", "tool_name": action}]
            actions = payload.get("actions")
            if isinstance(actions, list):
                return [
                    {"decision": "CALL_TOOL", "tool_name": action["toolName"]}
                    for action in actions
                    if isinstance(action, dict) and isinstance(action.get("toolName"), str)
                ]
        return []

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
