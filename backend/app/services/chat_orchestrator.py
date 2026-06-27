"""
ChatOrchestrator：keyword 意图分类 + 路由 GraphService / RAGService / fallback。

- 不使用 LLM 做意图分类
- 不从用户输入自由拼接数据库查询
- 商品名通过白名单提取，不识别则不匹配
"""

from __future__ import annotations

import json
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models.conversation import Conversation, Message
from app.schemas.conversation import MessageSource

# ---------------------------------------------------------------------------
# 虚构智家商品白名单
# ---------------------------------------------------------------------------
PRODUCT_WHITELIST: set[str] = {
    "Cam-A1",
    "Hub-Z1",
    "Sensor-T1",
    "Lock-D1",
    "Light-B1",
    "Plug-P1",
}

# ---------------------------------------------------------------------------
# 意图关键词规则（按优先级）
# ---------------------------------------------------------------------------
INTENT_RULES: list[tuple[str, list[str]]] = [
    ("compatibility",    ["兼容", "能不能一起", "搭配", "配对", "配合",
                          "能用吗", "一起用", "接到", "连到", "接上",
                          "能不能接", "支持.*吗", "联动", "能不能连"]),
    ("warranty_fault",   ["保修", "坏了", "故障", "维修", "延保", "不工作",
                          "有问题", "失灵", "打不开", "不亮", "没反应",
                          "不转了", "连不上", "掉线", "不报警"]),
    ("return_refund",    ["退货", "退款", "退换", "无理由", "退掉", "换货"]),
    ("shipping",         ["发货", "物流", "快递", "配送", "送货", "到哪了",
                          "几天能到", "什么时候到"]),
    ("order_status",     ["订单", "下单", "买.*到", "查.*进度"]),
    ("product_consultation", ["功能", "参数", "规格", "介绍", "颜色",
                               "价格", "多少钱", "有什么用", "能做什么",
                               "协议", "什么协议", "支持什么"]),
    ("complaint",        ["投诉", "态度", "不满", "差评", "很差"]),
    ("human_handoff",    ["转人工", "找人", "人工客服", "真人", "打电话",
                          "联系我"]),
]


def _extract_products(text: str) -> list[str]:
    """从用户输入中提取白名单内商品名（严格边界匹配）。

    - Cam-A1 和 Hub-Z1 兼容吗 → ["Cam-A1", "Hub-Z1"]
    - Cam-A10 / xxCam-A1 / Cam-A1-Pro → 不识别为 Cam-A1
    """
    found: list[str] = []
    for product in sorted(PRODUCT_WHITELIST, key=len, reverse=True):
        # 前后不能是字母/数字/连字符，防止 Cam-A10、xxCam-A1、Cam-A1-Pro 误匹配
        pattern = rf"(?<![a-zA-Z0-9\-]){re.escape(product)}(?![a-zA-Z0-9\-])"
        if re.search(pattern, text):
            found.append(product)
    return found


def classify_intent(text: str) -> tuple[str, float]:
    """
    基于关键词规则判定意图。
    返回 (intent_label, confidence)。
    confidence: 1.0 单条规则匹配，0.8 多条命中取第一条，0.5 无命中。
    """
    for intent, patterns in INTENT_RULES:
        for pattern in patterns:
            if re.search(pattern, text):
                return intent, 1.0
    return "human_handoff", 0.5


# ---------------------------------------------------------------------------
# Local demo fallback services
# ---------------------------------------------------------------------------
class _OfflineGraphService:
    """Fictional Zhijia graph used only when DEMO_OFFLINE_MODE=true."""

    _compatibility = {
        ("Cam-A1", "Hub-Z1"): True,
        ("Hub-Z1", "Cam-A1"): True,
        ("Sensor-T1", "Hub-Z1"): True,
        ("Hub-Z1", "Sensor-T1"): True,
        ("Lock-D1", "Hub-Z1"): True,
        ("Hub-Z1", "Lock-D1"): True,
    }
    _protocols = {
        "Cam-A1": ["WiFi", "Zigbee"],
        "Hub-Z1": ["WiFi", "Zigbee", "Bluetooth"],
        "Sensor-T1": ["Zigbee"],
        "Lock-D1": ["Zigbee", "Bluetooth"],
        "Light-B1": ["WiFi"],
        "Plug-P1": ["WiFi"],
    }
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
        "Sensor-T1": {
            "policy_name": "标准保修",
            "duration": "1 年",
            "description": "传感器主体一年内非人为故障免费维修。",
        },
    }

    @staticmethod
    def _validate(name: str) -> None:
        if name not in PRODUCT_WHITELIST:
            raise ValueError(f"未识别的商品 '{name}'")

    def check_compatibility(self, product_a: str, product_b: str) -> bool:
        self._validate(product_a)
        self._validate(product_b)
        return self._compatibility.get((product_a, product_b), False)

    def get_protocols(self, product_name: str) -> list[str]:
        self._validate(product_name)
        return self._protocols.get(product_name, [])

    def get_warranty(self, product_name: str) -> dict | None:
        self._validate(product_name)
        return self._warranty.get(product_name)


class _OfflineRagService:
    """Fictional policy snippets used only when DEMO_OFFLINE_MODE=true."""

    def search(self, query: str, top_k: int = 3) -> dict:
        snippet = "购买后七日内支持无理由退货，商品需保持完好、配件齐全且不影响二次销售。"
        return {
            "query": query,
            "answer": (
                f"根据已上传的智家政策文档，关于「{query}」找到以下相关信息：\n"
                f"1. [智家售后政策.pdf | 第 3 页] {snippet}\n"
                "（以上信息来源于本地演示文档片段。）"
            ),
            "source_type": "document_rag",
            "sources": [
                {
                    "document_name": "智家售后政策.pdf",
                    "location": "第 3 页",
                    "snippet": snippet,
                }
            ],
            "chunk_count": 1,
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class ChatOrchestrator:
    """
    聊天编排器。
    构造时可注入 fake GraphService / RAGService 用于测试。
    """

    def __init__(
        self,
        graph_service=None,
        rag_service=None,
    ) -> None:
        # 惰性导入真实服务，避免测试环境依赖
        if graph_service is not None:
            self._graph = graph_service
        else:
            self._graph = None  # 延迟初始化

        if rag_service is not None:
            self._rag = rag_service
        else:
            self._rag = None

    @property
    def graph(self):
        if self._graph is None:
            from app.config import settings

            if settings.demo_offline_mode:
                self._graph = _OfflineGraphService()
            else:
                from app.services.graph_service import GraphService
                from neo4j import GraphDatabase

                driver = GraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, settings.neo4j_password),
                )
                self._graph = GraphService(driver)
        return self._graph

    @property
    def rag(self):
        if self._rag is None:
            from app.config import settings

            if settings.demo_offline_mode:
                self._rag = _OfflineRagService()
            else:
                from app.services.rag_service import RagService

                self._rag = RagService()
        return self._rag

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------
    def route(
        self,
        message: str,
        conversation_id: int,
        db: Session,
    ) -> dict:
        """
        完整编排：分类 → 路由 → 保存 → 返回 ChatResponse 字典。
        """
        intent, confidence = classify_intent(message)
        products = _extract_products(message)

        answer: str
        source_type: str
        sources: list[MessageSource]
        handoff_required: bool = False

        # ------------------------------------------------------------------
        # compatibility — 需要两个商品名
        # ------------------------------------------------------------------
        if intent == "compatibility" and len(products) >= 2:
            a, b = products[0], products[1]
            try:
                compatible = self.graph.check_compatibility(a, b)
                relation = f"{a} COMPATIBLE_WITH {b}" if compatible else f"{a} NOT_COMPATIBLE_WITH {b}"
                answer = f"{a} 与 {b} {'兼容' if compatible else '不兼容'}。"
                source_type = "knowledge_graph"
                sources = [MessageSource(
                    source_type="knowledge_graph",
                    relation=relation,
                )]
            except ValueError as e:
                answer = str(e)
                source_type = "knowledge_graph"
                sources = []
                handoff_required = True
        elif intent == "compatibility":
            # 只有一个或不明确商品 → 无法查询
            answer = "请提供两个智家商品名称（如 Cam-A1、Hub-Z1），我来帮您查询兼容性。"
            source_type = "knowledge_graph"
            sources = []
            handoff_required = True

        # ------------------------------------------------------------------
        # warranty_fault — 优先查图谱保修政策，无则 fallback
        # ------------------------------------------------------------------
        elif intent == "warranty_fault" and products:
            prod = products[0]
            try:
                warranty = self.graph.get_warranty(prod)
                if warranty:
                    answer = (
                        f"{prod} 适用 {warranty['policy_name']}，"
                        f"保修期 {warranty['duration']}。\n"
                        f"{warranty['description']}"
                    )
                    source_type = "knowledge_graph"
                    sources = [MessageSource(
                        source_type="knowledge_graph",
                        relation=f"{prod} COVERED_BY {warranty['policy_name']}",
                    )]
                else:
                    answer = f"未找到 {prod} 的保修信息。建议联系人工客服确认。"
                    source_type = "knowledge_graph"
                    sources = []
            except ValueError as e:
                answer = str(e)
                source_type = "knowledge_graph"
                sources = []
                handoff_required = True

        # ------------------------------------------------------------------
        # return_refund / 政策类 — RAGService
        # ------------------------------------------------------------------
        elif intent == "return_refund":
            try:
                result = self.rag.search(message, top_k=3)
                answer = result["answer"]
                source_type = "document_rag"
                sources = [
                    MessageSource(
                        source_type="document_rag",
                        document_name=s.get("document_name"),
                        location=s.get("location"),
                        snippet=s.get("snippet"),
                    )
                    for s in result.get("sources", [])
                ]
            except Exception:
                answer = "暂无可检索的文档。请先上传相关政策文档后再提问。"
                source_type = "document_rag"
                sources = []
                handoff_required = True

        # ------------------------------------------------------------------
        # product_consultation — 如有商品名则调用 GraphService.get_protocols()
        # （intent 为 product_consultation，source_type 为 knowledge_graph）
        # ------------------------------------------------------------------
        elif intent == "product_consultation" and products:
            prod = products[0]
            try:
                protocols = self.graph.get_protocols(prod)
                if protocols:
                    answer = f"{prod} 支持的协议: {', '.join(protocols)}。"
                else:
                    answer = f"{prod} 暂无协议信息。"
                source_type = "knowledge_graph"
                sources = [MessageSource(
                    source_type="knowledge_graph",
                    relation=f"{prod} SUPPORTS {'/'.join(protocols)}" if protocols else f"{prod} SUPPORTS (none)",
                )]
            except ValueError as e:
                answer = str(e)
                source_type = "knowledge_graph"
                sources = []
                handoff_required = True

        # ------------------------------------------------------------------
        # shipping / order_status — 尚无真实订单系统，fallback
        # ------------------------------------------------------------------
        elif intent in ("shipping", "order_status"):
            answer = (
                "订单查询功能尚未接入。如需查询订单状态或物流信息，"
                "请联系人工客服或稍后再试。"
            )
            source_type = "fallback"
            sources = []
            handoff_required = True

        # ------------------------------------------------------------------
        # fallback / complaint / human_handoff
        # ------------------------------------------------------------------
        else:
            answer = (
                "抱歉，我暂时无法处理这个问题。建议转接人工客服获取帮助。"
            )
            source_type = "fallback"
            sources = []
            handoff_required = True

        # ------------------------------------------------------------------
        # 保存消息
        # ------------------------------------------------------------------
        self._save_messages(
            db,
            conversation_id=conversation_id,
            user_text=message,
            assistant_text=answer,
            intent=intent,
            confidence=int(confidence * 100),
            source_type=source_type,
            sources=sources,
            handoff_required=handoff_required,
        )

        return {
            "conversation_id": conversation_id,
            "answer": answer,
            "intent": intent,
            "confidence": confidence,
            "source_type": source_type,
            "sources": sources,
            "handoff_required": handoff_required,
        }

    # ------------------------------------------------------------------
    # 消息持久化
    # ------------------------------------------------------------------
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
        # user message
        user_msg = Message(
            conversation_id=conversation_id,
            role="user",
            content=user_text,
        )
        db.add(user_msg)

        # assistant message
        sources_json = json.dumps(
            [s.model_dump() for s in sources], ensure_ascii=False
        )
        assistant_msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_text,
            intent=intent,
            confidence=confidence,
            source_type=source_type,
            sources_json=sources_json,
            handoff_required=handoff_required,
        )
        db.add(assistant_msg)
        db.commit()
