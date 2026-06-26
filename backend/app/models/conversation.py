"""
MySQL 模型: conversations + messages
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey, func
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    demo_user_id = Column(Integer, nullable=False, default=1)
    title = Column(String(255), nullable=False, default="新会话")
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<Conversation id={self.id} title={self.title!r}>"


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    role = Column(String(16), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    intent = Column(String(64), nullable=True)          # 分类器判定意图
    confidence = Column(Integer, nullable=True)          # 0-100 整数
    source_type = Column(String(32), nullable=True)      # "knowledge_graph" | "document_rag" | "fallback"
    sources_json = Column(Text, nullable=True)            # JSON 字符串
    handoff_required = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<Message id={self.id} conv={self.conversation_id} "
            f"role={self.role!r}>"
        )
