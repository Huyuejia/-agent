"""Application-owned contracts for controlled Agent executions."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.retrieval.domain import Evidence


class ExecutionMode(str, Enum):
    WORKFLOW = "workflow"
    AGENT = "agent"


class TaskStatus(str, Enum):
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ToolStatus(str, Enum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"


class FailureCategory(str, Enum):
    TASK_UNDERSTANDING = "TASK_UNDERSTANDING"
    NEXT_ACTION = "NEXT_ACTION"
    WRONG_TOOL = "WRONG_TOOL"
    WRONG_TOOL_ARGS = "WRONG_TOOL_ARGS"
    TOOL_OR_RETRIEVAL = "TOOL_OR_RETRIEVAL"
    STATE_LOSS = "STATE_LOSS"
    VERIFICATION = "VERIFICATION"
    FINAL_ANSWER = "FINAL_ANSWER"
    UNATTRIBUTED = "UNATTRIBUTED"


class ExecutionDecision(BaseModel):
    mode: ExecutionMode
    reason_code: str


class ToolResult(BaseModel):
    status: ToolStatus
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    retryable: bool = False
    error_code: str | None = None
    latency_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _error_has_code(self) -> "ToolResult":
        if self.status is ToolStatus.ERROR and not self.error_code:
            raise ValueError("ERROR ToolResult requires error_code")
        return self


class ToolObservation(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult


class TaskState(BaseModel):
    task_id: str
    conversation_id: int
    objective: str
    known_facts: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    requested_fields: list[str] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    tool_observations: list[ToolObservation] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    retry_info: dict[str, int] = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.RUNNING

    def observe(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> None:
        self.tool_observations.append(
            ToolObservation(
                tool_name=tool_name,
                arguments=arguments,
                result=result,
            )
        )
        self.completed_steps.append(tool_name)
        for evidence in result.evidence:
            if evidence.evidence_id not in self.evidence_refs:
                self.evidence_refs.append(evidence.evidence_id)

    def transition(self, status: TaskStatus, *, verified: bool = False) -> None:
        if status is TaskStatus.SUCCEEDED and not verified:
            raise ValueError("SUCCEEDED requires application verification")
        self.status = status

    def wait_for_user(self, clarification_text: str, requested_fields: list[str]) -> None:
        if not clarification_text or not requested_fields:
            raise ValueError("WAITING_FOR_USER requires clarification and requested fields")
        self.missing_information = requested_fields
        self.requested_fields = requested_fields
        self.transition(TaskStatus.WAITING_FOR_USER)

    def resume_with_user_message(self, message: str) -> None:
        if self.status is not TaskStatus.WAITING_FOR_USER:
            raise ValueError("only WAITING_FOR_USER tasks can resume")
        self.known_facts.append({"source": "user", "text": message})
        self.missing_information = []
        self.requested_fields = []

        self.transition(TaskStatus.RUNNING)

class AgentCandidate(BaseModel):
    answer: str = Field(min_length=1)
    intent: str = "agent_dynamic_task"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_type: str
    evidence_refs: list[str] = Field(default_factory=list)
    handoff_required: bool = False


class RuntimeEvent(BaseModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None


class RuntimeResult(BaseModel):
    candidate: AgentCandidate | None = None
    clarification_text: str | None = None
    requested_fields: list[str] = Field(default_factory=list)
    events: list[RuntimeEvent] = Field(default_factory=list)
    error_code: str | None = None

    @model_validator(mode="after")
    def _has_one_terminal_result(self) -> "RuntimeResult":
        if self.candidate is not None:
            return self
        if self.clarification_text and self.requested_fields:
            return self
        if self.error_code:
            return self
        raise ValueError("RuntimeResult requires candidate, clarification, or error")


class VerificationResult(BaseModel):
    accepted: bool
    error_code: str | None = None
    violations: list[str] = Field(default_factory=list)
