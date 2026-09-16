"""Persistent application state and ordered trace for controlled Agent runs."""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.models.base import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(String(36), primary_key=True)
    conversation_id = Column(
        Integer, ForeignKey("conversations.id"), nullable=False, index=True
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    request_id = Column(String(64), nullable=False)
    objective = Column(Text, nullable=False)
    execution_mode = Column(String(16), nullable=False, default="agent")
    status = Column(String(32), nullable=False)
    task_state = Column(JSON, nullable=False)
    final_answer = Column(Text, nullable=True)
    failure_category = Column(String(64), nullable=True)
    runtime_version = Column(String(128), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)


class AgentTraceEvent(Base):
    __tablename__ = "agent_trace_events"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "sequence_number", name="uq_agent_trace_run_sequence"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence_number = Column(Integer, nullable=False)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    latency_ms = Column(Integer, nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
