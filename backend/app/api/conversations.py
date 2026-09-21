"""
会话与聊天 API
POST /api/conversations      — 创建会话
POST /api/chat               — 发送消息并获取回答
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies.auth import get_current_user
from app.postgres_database import get_postgres_db
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.schemas.conversation import (
    ChatRequest,
    ChatResponse,
    ConversationCreateResponse,
)
from app.services.chat_orchestrator import ChatOrchestrator
from app.services.legacy_chat import (
    FallbackIntentClassifier,
    RuleBasedIntentClassifier,
    create_legacy_chat_service,
)
from app.services.intent_model_client import HttpIntentClassifier

router = APIRouter(tags=["conversations"])

# 惰性编排器 — 测试可注入 fake
_orchestrator: ChatOrchestrator | None = None


def _get_orchestrator() -> ChatOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        legacy_service = None
        retrieval_service = None
        agent_service = None
        if settings.demo_offline_mode:
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
            legacy_service = create_legacy_chat_service(
                classifier=classifier,
                offline_mode=True,
            )
        else:
            from app.agent.factory import create_agent_task_service
            from app.services.retrieval_chat import create_retrieval_chat_service

            retrieval_service = create_retrieval_chat_service()
            agent_service = create_agent_task_service(retrieval_service)
        _orchestrator = ChatOrchestrator(
            retrieval_service=retrieval_service,
            agent_service=agent_service,
            legacy_service=legacy_service,
        )
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
    db: Session = Depends(get_postgres_db),
    current_user: User = Depends(get_current_user),
):
    """创建新会话。"""
    conv = Conversation(title=title[:255], user_id=current_user.id)
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
    db: Session = Depends(get_postgres_db),
    current_user: User = Depends(get_current_user),
):
    """发送消息，获取意图、来源引用和回答。"""
    # 1. 同时校验存在性与归属；跨用户和不存在都返回 404。
    conv = db.scalar(
        select(Conversation).where(
            Conversation.id == payload.conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
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
