"""
Pydantic schemas: 会话创建 / 发送消息 / 响应
"""

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
class ConversationCreateResponse(BaseModel):
    conversation_id: int
    title: str
    message: str


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话 ID")
    message: str = Field(..., min_length=1, max_length=1000, description="用户消息")


class MessageSource(BaseModel):
    source_type: str  # "knowledge_graph" | "document_rag" | "exact"
    document_name: str | None = None
    location: str | None = None
    snippet: str | None = None
    relation: str | None = None  # 图谱关系三元组，如 "Cam-A1 COMPATIBLE_WITH Hub-Z1"


class ChatResponse(BaseModel):
    conversation_id: int
    answer: str
    intent: str
    confidence: float
    source_type: str
    sources: list[MessageSource]
    handoff_required: bool
    execution_mode: str | None = None
    agent_run_id: str | None = None
    task_status: str | None = None
    needs_user_input: bool = False
    requested_fields: list[str] = Field(default_factory=list)
