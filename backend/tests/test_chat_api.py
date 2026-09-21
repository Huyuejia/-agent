"""
会话与聊天编排集成测试。

- GraphService 用 fake/mock，不依赖 Neo4j
- 旧编排降级用 fake，不依赖真实检索后端
- 数据库用内存 SQLite
- 不调 Qwen API、不加载模型
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_db
from app.models.document import Base as DocBase
from app.models.conversation import Base as ConvBase
from app.schemas.conversation import MessageSource


# ===================================================================
# Fake GraphService
# ===================================================================
class FakeGraphService:
    """内存中的虚拟图谱服务，覆盖白名单内产品。"""

    WHITELIST = {"Cam-A1", "Hub-Z1", "Sensor-T1", "Lock-D1", "Light-B1", "Plug-P1"}

    # 兼容关系
    _compatibility = {
        ("Cam-A1", "Hub-Z1"): True,
        ("Hub-Z1", "Cam-A1"): True,
        ("Sensor-T1", "Hub-Z1"): True,
        ("Hub-Z1", "Sensor-T1"): True,
        ("Lock-D1", "Hub-Z1"): True,
        ("Hub-Z1", "Lock-D1"): True,
    }

    # 协议
    _protocols = {
        "Cam-A1": ["WiFi", "Zigbee"],
        "Hub-Z1": ["WiFi", "Zigbee", "Bluetooth"],
        "Sensor-T1": ["Zigbee"],
        "Lock-D1": ["Zigbee", "Bluetooth"],
        "Light-B1": ["WiFi"],
        "Plug-P1": ["WiFi"],
    }

    # 保修
    _warranty = {
        "Cam-A1": {
            "policy_name": "标准保修",
            "duration": "1 年",
            "description": "非人为损坏免费维修，人为损坏付费维修。",
        },
        "Hub-Z1": {
            "policy_name": "尊享保修",
            "duration": "3 年",
            "description": "三年内免费上门换新。",
        },
    }

    def check_compatibility(self, a: str, b: str) -> bool:
        if a not in self.WHITELIST or b not in self.WHITELIST:
            raise ValueError(f"未识别的商品")
        return self._compatibility.get((a, b), False)

    def get_protocols(self, name: str) -> list[str]:
        if name not in self.WHITELIST:
            raise ValueError(f"未识别的商品 '{name}'")
        return self._protocols.get(name, [])

    def get_warranty(self, name: str) -> dict | None:
        if name not in self.WHITELIST:
            raise ValueError(f"未识别的商品 '{name}'")
        return self._warranty.get(name)


# ===================================================================
# Fake RAGService
# ===================================================================
class FakeRAGService:
    """返回固定结果的虚拟文档结果，不依赖真实检索后端。"""

    def search(self, query: str, top_k: int = 3) -> dict:
        snippet = (
            "用户可在购买后七日内无理由退货，前提是商品完好、配件齐全且不影响二次销售。"
        )
        return {
            "query": query,
            "answer": (
                f"根据已上传的文档，关于「{query}」找到以下相关信息：\n"
                f"1. [智家保修政策.pdf | 第 3 页] {snippet}\n"
                "（以上信息来源于已上传文档，仅供参考。）"
            ),
            "source_type": "document_rag",
            "sources": [
                {
                    "document_name": "智家保修政策.pdf",
                    "location": "第 3 页",
                    "snippet": snippet,
                }
            ],
            "chunk_count": 1,
        }


# ===================================================================
# TestClient + 依赖注入
# ===================================================================
@pytest.fixture(scope="module")
def client():
    """创建 TestClient，注入 fake GraphService / RAGService + 内存 SQLite。"""
    from app.main import create_app
    from app.services.legacy_chat import LegacyChatService, RuleBasedIntentClassifier
    from app.services.chat_orchestrator import ChatOrchestrator

    app = create_app(initialize_database=False)

    # 内存 SQLite
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # 用 tables= 限制建表范围，避免共享 Base.metadata 里其它 PostgreSQL 专有表
    # （如 document_chunks）污染 SQLite 建表（CompileError）。
    from app.models.conversation import Conversation, Message
    from app.models.user import User

    DocBase.metadata.create_all(
        bind=test_engine,
        tables=[
            User.__table__,
            Conversation.__table__,
            Message.__table__,
        ],
    )
    TestingSessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)


    auth_db = TestingSessionLocal()
    user = User(
        email="chat@example.com",
        normalized_email="chat@example.com",
        password_hash="test-only",
        role="user",
        is_active=True,
    )
    auth_db.add(user)
    auth_db.commit()
    auth_db.refresh(user)
    user_id = user.id
    auth_db.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # 注入 fake 编排器
    import app.api.conversations as conv_mod

    fake_orch = ChatOrchestrator(
        legacy_service=LegacyChatService(
            classifier=RuleBasedIntentClassifier(),
            graph_service=FakeGraphService(),
            rag_service=FakeRAGService(),
        ),
    )
    conv_mod._orchestrator = fake_orch

    with TestClient(app) as tc:
        from app.security.jwt import create_access_token

        tc.headers.update({"Authorization": f"Bearer {create_access_token(user_id)}"})
        yield tc

    app.dependency_overrides.clear()
    conv_mod._orchestrator = None


# ===================================================================
# 会话创建
# ===================================================================
class TestCreateConversation:
    def test_create_success(self, client):
        resp = client.post("/api/conversations?title=测试会话")
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["conversation_id"] > 0
        assert data["title"] == "测试会话"
        assert "创建成功" in data["message"]


# ===================================================================
# 兼容性路由
# ===================================================================
class TestCompatibilityRoute:
    def test_known_compatible_pair(self, client):
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "Cam-A1和Hub-Z1能兼容吗",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["intent"] == "compatibility"
        assert data["source_type"] == "knowledge_graph"
        assert "兼容" in data["answer"]
        assert len(data["sources"]) == 1
        assert "COMPATIBLE_WITH" in data["sources"][0]["relation"]

    def test_unknown_product_compatibility_requires_agent(self, client):
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "X99和Y99兼容吗",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["execution_mode"] == "agent"
        assert data["intent"] == "agent_unavailable"
        assert data["handoff_required"] is True

    def test_single_product_no_pair(self, client):
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "Cam-A1兼容吗",
        })
        assert resp.status_code == 200, resp.text
        assert "两个智家商品名称" in resp.json()["answer"]


# ===================================================================
# 协议路由
# ===================================================================
class TestProtocolRoute:
    def test_protocol_keyword_in_product_consultation(self, client):
        """「支持什么协议」命中 product_consultation → 查协议。"""
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "Hub-Z1支持什么协议",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["intent"] == "product_consultation"
        assert data["source_type"] == "knowledge_graph"
        assert "Zigbee" in data["answer"] or "WiFi" in data["answer"]


# ===================================================================
# 保修路由
# ===================================================================
class TestWarrantyRoute:
    def test_warranty_with_product(self, client):
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "Cam-A1保修多久",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["intent"] == "warranty_fault"
        assert data["source_type"] == "knowledge_graph"
        assert "保修" in data["answer"]
        assert len(data["sources"]) >= 1


# ===================================================================
# 文档 RAG 路由（退货/政策类）
# ===================================================================
class TestDocumentRagRoute:
    def test_return_refund_goes_to_rag(self, client):
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "怎么退货",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["intent"] == "return_refund"
        assert data["source_type"] == "document_rag"
        assert "退货" in data["answer"] or "无理由" in data["answer"]

    def test_policy_keyword(self, client):
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "退款政策是什么",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["intent"] == "return_refund"


# ===================================================================
# Fallback
# ===================================================================
class TestFallbackRoute:
    def test_unrecognized_handoff(self, client):
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "今天天气怎么样",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["intent"] == "human_handoff"
        assert data["handoff_required"] is True
        assert data["source_type"] == "fallback"

    def test_complaint_handoff(self, client):
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "我要投诉你们的客服态度",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["intent"] == "complaint"
        assert data["handoff_required"] is True


# ===================================================================
# 消息持久化
# ===================================================================
class TestMessagePersistence:
    def test_both_messages_saved(self, client):
        cid = _create_conv(client)
        client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "Cam-A1保修多久",
        })
        # 通过 overridden dependency 获取 session 查询消息
        from app.database import get_db as get_db_fn
        from app.models.conversation import Message as MsgModel

        override_fn = client.app.dependency_overrides[get_db_fn]
        db = next(override_fn())
        try:
            msgs = (
                db.query(MsgModel)
                .filter_by(conversation_id=cid)
                .order_by(MsgModel.id)
                .all()
            )
            assert len(msgs) == 2
            assert msgs[0].role == "user"
            assert msgs[0].content == "Cam-A1保修多久"
            assert msgs[1].role == "assistant"
            assert msgs[1].intent == "warranty_fault"
            assert msgs[1].handoff_required is False
            # 验证 sources_json 合法
            sources = json.loads(msgs[1].sources_json)
            assert isinstance(sources, list)
        finally:
            db.close()


# ===================================================================
# 会话不存在
# ===================================================================
class TestConversationNotFound:
    def test_nonexistent_conversation(self, client):
        resp = client.post("/api/chat", json={
            "conversation_id": 99999,
            "message": "hello",
        })
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]


# ===================================================================
# 严格商品白名单匹配
# ===================================================================
class TestStrictProductMatching:
    def test_cam_a10_not_mistaken_for_cam_a1(self, client):
        """Cam-A10 不是 Cam-A1，仅 Hub-Z1 被识别，兼容性缺第二个产品→handoff。"""
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "Cam-A10和Hub-Z1兼容吗",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["intent"] == "compatibility"
        assert data["handoff_required"] is True
        assert "两个智家商品名称" in data["answer"]

    def test_xxcam_a1_not_recognized(self, client):
        """xxCam-A1 不是白名单内产品名。"""
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "xxCam-A1和Hub-Z1兼容吗",
        })
        data = resp.json()
        assert data["handoff_required"] is True
        assert "两个智家商品名称" in data["answer"]

    def test_cam_a1_pro_not_recognized(self, client):
        """Cam-A1-Pro 不是 Cam-A1。"""
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "Cam-A1-Pro和Hub-Z1兼容吗",
        })
        data = resp.json()
        assert data["handoff_required"] is True
        assert "两个智家商品名称" in data["answer"]

    def test_cam_a1_with_chinese_context_recognized(self, client):
        """中文前后文中的 Cam-A1 仍应被正确识别：使用Cam-A1需要什么。"""
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "使用Cam-A1需要什么协议",
        })
        data = resp.json()
        # product_consultation + Cam-A1 → 调用 get_protocols
        assert data["intent"] == "product_consultation"
        assert data["source_type"] == "knowledge_graph"
        assert "Cam-A1" in data["answer"]


# ===================================================================
# 路由标注一致性：协议查询 → product_consultation + knowledge_graph
# ===================================================================
class TestProtocolRoutingLabel:
    def test_protocol_query_intent_is_product_consultation(self, client):
        """Hub-Z1支持什么协议 → intent=product_consultation, source_type=knowledge_graph。"""
        cid = _create_conv(client)
        resp = client.post("/api/chat", json={
            "conversation_id": cid,
            "message": "Hub-Z1支持什么协议",
        })
        data = resp.json()
        assert data["intent"] == "product_consultation", (
            f"协议查询 intent 应为 product_consultation，实际 {data['intent']}"
        )
        assert data["source_type"] == "knowledge_graph"
        assert "Zigbee" in data["answer"] or "WiFi" in data["answer"]


# ===================================================================
# 辅助
# ===================================================================
def _create_conv(client, title="测试会话") -> int:
    resp = client.post(f"/api/conversations?title={title}")
    assert resp.status_code == 201
    return resp.json()["conversation_id"]
