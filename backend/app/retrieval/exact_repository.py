"""ExactRepository 协议：按标准化标识符精确查询业务表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class ResolvedEntity:
    """一个显式标识符解析到的一条数据库记录。"""

    entity_type: str        # "sku" | "order" | "serial" | "error_code"
    normalized_value: str   # 查询用的标准化值
    record_id: str          # 数据库稳定 UUID
    display_identifier: str  # 原始展示标识符（sku / order_no / serial / code）
    table: str              # "products" | "orders" | "devices" | "error_codes"


class ExactRepository(Protocol):
    """精确检索仓储协议。

    entity_types / entity_values 为并行列表（来自 RetrievalStep.filters）。
    未命中的标识符不返回任何记录；返回空列表由调用方按 MissPolicy 处理。
    """

    def resolve(
        self,
        entity_types: Sequence[str],
        entity_values: Sequence[str],
    ) -> list[ResolvedEntity]:
        ...
