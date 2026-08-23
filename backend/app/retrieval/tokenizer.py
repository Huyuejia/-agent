"""中文分词抽象与 Jieba 实现。

索引文档与查询必须使用同一个 tokenizer，保证 lexical_text 与查询分词一致。
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

import jieba

# 领域词典：至少包含六个 SKU、产品名与常见领域词。
DEFAULT_CUSTOM_WORDS: tuple[str, ...] = (
    # 六个智家 SKU
    "Cam-A1",
    "Hub-Z1",
    "Sensor-T1",
    "Lock-D1",
    "Light-B1",
    "Plug-P1",
    # 产品名
    "智家智能摄像头",
    "智家智能网关",
    "智家温湿度传感器",
    "智家智能门锁",
    "智家智能灯泡",
    "智家智能插座",
    # 领域词
    "Zigbee",
    "WiFi",
    "蓝牙",
    "保修",
    "退货",
)


@runtime_checkable
class Tokenizer(Protocol):
    """分词器协议：把文本切成词元列表（不含空白）。"""

    def tokenize(self, text: str) -> list[str]:
        ...


class JiebaTokenizer:
    """基于 jieba.cut_for_search 的分词器（搜索引擎细粒度模式）。"""

    def __init__(self, extra_words: Sequence[str] | None = None) -> None:
        for word in DEFAULT_CUSTOM_WORDS:
            jieba.add_word(word)
        for word in extra_words or ():
            jieba.add_word(word)

    def tokenize(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        return [token.strip() for token in jieba.cut_for_search(text) if token.strip()]
