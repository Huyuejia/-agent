"""
会话与聊天 API
POST /api/conversations      — 创建会话
POST /api/chat               — 发送消息并获取回答
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.conversation import Conversation, Message
from app.schemas.conversation import (
    ChatRequest,
    ChatResponse,
    ConversationCreateResponse,
)
from app.services.chat_orchestrator import (
    ChatOrchestrator,
    FallbackIntentClassifier,
    RuleBasedIntentClassifier,
)
from app.services.intent_model_client import HttpIntentClassifier

router = APIRouter(tags=["conversations"])

# 惰性编排器 — 测试可注入 fake
_orchestrator: ChatOrchestrator | None = None


def _get_orchestrator() -> ChatOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        classifier = RuleBasedIntentClassifier()
        if settings.intent_model_url:
            model_classifier = HttpIntentClassifier(
                base_url=settings.intent_model_url,
                timeout_seconds=settings.intent_model_timeout_seconds,
            )
            classifier = FallbackIntentClassifier(
                primary=model_classifier,
                fallback=classifier,
            )
        _orchestrator = ChatOrchestrator(classifier=classifier)
    return _orchestrator


# ---------------------------------------------------------------------------
# POST /api/conversations
# ---------------------------------------------------------------------------
@router.post(
    "/api/conversations",
    response_model=ConversationCreateResponse,
    status_code=201,
)
def create_conversation(
    title: str = "新会话",
    db: Session = Depends(get_db),
):
    """创建新会话。"""
    conv = Conversation(title=title[:255])
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return ConversationCreateResponse(
        conversation_id=conv.id,
        title=conv.title,
        message="会话创建成功",
    )


# ---------------------------------------------------------------------------
# POST /api/chat
# ---------------------------------------------------------------------------
@router.post("/api/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
):
    """发送消息，获取意图、来源引用和回答。"""
    # 1. 校验会话存在
    conv = db.get(Conversation, payload.conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=404,
            detail=f"会话 {payload.conversation_id} 不存在",
        )

    # 2. 编排
    orchestrator = _get_orchestrator()
    result = orchestrator.route(
        message=payload.message,
        conversation_id=payload.conversation_id,
        db=db,
    )

    return ChatResponse(**result)
