"""Chat request orchestration and conversation message persistence."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agent.domain import ExecutionMode
from app.agent.routing import ExecutionRouter
from app.models.conversation import Conversation, Message
from app.observability import current_request_id
from app.schemas.conversation import MessageSource
from app.services.legacy_chat import LegacyChatService

logger = logging.getLogger(__name__)


class ChatOrchestrator:
    """Coordinate one chat request without implementing business workflows."""

    def __init__(
        self,
        retrieval_service=None,
        agent_service=None,
        execution_router=None,
        legacy_service: LegacyChatService | None = None,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._agent_service = agent_service
        self._execution_router = execution_router or ExecutionRouter()
        self._legacy_service = legacy_service

    def route(
        self,
        message: str,
        conversation_id: int,
        db: Session,
        user_id: int | None = None,
        request_id: str = "",
        resume_fields: dict[str, str] | None = None,
    ) -> dict:
        """Choose the execution path, persist the result, and return it."""
        if self._agent_service is not None:
            if user_id is None:
                conversation = db.get(Conversation, conversation_id)
                if conversation is None:
                    raise ValueError("conversation does not exist")
                user_id = conversation.user_id
            result = self._agent_service.try_resume(
                message=message,
                conversation_id=conversation_id,
                user_id=user_id,
                request_id=request_id or current_request_id(),
                resume_fields=resume_fields,
                db=db,
            )
            if result is not None:
                self._save_result(db, conversation_id, message, result)
                return {"conversation_id": conversation_id, **result}

        decision = self._execution_router.decide(message)
        if decision.mode is ExecutionMode.AGENT:
            if self._agent_service is None:
                result = {
                    "answer": "Agent 运行时尚未配置，请稍后重试或联系人工客服。",
                    "intent": "agent_unavailable",
                    "confidence": 0.0,
                    "source_type": "fallback",
                    "sources": [],
                    "handoff_required": True,
                    "execution_mode": "agent",
                    "task_status": "FAILED",
                }
            else:
                result = self._agent_service.execute(
                    objective=message,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    request_id=request_id or current_request_id(),
                    boundary_reason=decision.reason_code,
                    db=db,
                )
            self._save_result(db, conversation_id, message, result)
            return {"conversation_id": conversation_id, **result}

        if self._retrieval_service is not None:
            try:
                result = self._retrieval_service.answer(message)
            except Exception:
                logger.exception("Unified retrieval pipeline failed")
                result = {
                    "answer": (
                        "检索服务暂时不可用。为避免给出未经证据支持的答案，"
                        "建议稍后重试或联系人工客服。"
                    ),
                    "intent": "retrieval_error",
                    "confidence": 0.0,
                    "source_type": "fallback",
                    "sources": [],
                    "handoff_required": True,
                }

            self._save_result(db, conversation_id, message, result)
            return {
                "conversation_id": conversation_id,
                "answer": result["answer"],
                "intent": result["intent"],
                "confidence": result["confidence"],
                "source_type": result["source_type"],
                "sources": result["sources"],
                "handoff_required": result["handoff_required"],
                "execution_mode": "workflow",
            }

        if self._legacy_service is None:
            raise RuntimeError("legacy workflow service is not configured")
        result = self._legacy_service.execute(message)
        self._save_result(db, conversation_id, message, result)
        return {"conversation_id": conversation_id, **result}

    @classmethod
    def _save_result(
        cls,
        db: Session,
        conversation_id: int,
        user_text: str,
        result: dict,
    ) -> None:
        cls._save_messages(
            db,
            conversation_id=conversation_id,
            user_text=user_text,
            assistant_text=result["answer"],
            intent=result["intent"],
            confidence=int(result["confidence"] * 100),
            source_type=result["source_type"],
            sources=result["sources"],
            handoff_required=result["handoff_required"],
        )

    @staticmethod
    def _save_messages(
        db: Session,
        *,
        conversation_id: int,
        user_text: str,
        assistant_text: str,
        intent: str,
        confidence: int,
        source_type: str,
        sources: list[MessageSource],
        handoff_required: bool,
    ) -> None:
        db.add(
            Message(
                conversation_id=conversation_id,
                role="user",
                content=user_text,
            )
        )
        sources_json = json.dumps(
            [source.model_dump() for source in sources], ensure_ascii=False
        )
        db.add(
            Message(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_text,
                intent=intent,
                confidence=confidence,
                source_type=source_type,
                sources_json=sources_json,
                handoff_required=handoff_required,
            )
        )
        db.commit()
