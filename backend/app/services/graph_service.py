"""
固定 Cypher 模板 + 参数化 Neo4j 图谱查询服务。
LLM 不生成任何 Cypher；所有查询均通过白名单校验 + 参数绑定。
"""

from neo4j import GraphDatabase, Driver

# ---------------------------------------------------------------------------
# 产品名白名单 — 唯一允许传入 Cypher 参数的商品标识
# ---------------------------------------------------------------------------
PRODUCT_WHITELIST: set[str] = {
    "Cam-A1",
    "Hub-Z1",
    "Sensor-T1",
    "Lock-D1",
    "Light-B1",
    "Plug-P1",
}


def _validate_product(name: str) -> str:
    """白名单校验：不在白名单内直接拒绝，不执行任何 Cypher。"""
    if not isinstance(name, str) or name not in PRODUCT_WHITELIST:
        raise ValueError(
            f"未识别的商品 '{name}'，仅支持: {', '.join(sorted(PRODUCT_WHITELIST))}"
        )
    return name


# ---------------------------------------------------------------------------
# 固定 Cypher 模板（参数占位符均为 $param，绝不拼接用户输入）
# ---------------------------------------------------------------------------

# 模板 1：检查两个商品是否直接兼容
_TEMPLATE_COMPATIBILITY = """
MATCH (a:Product {name: $product_a})-[r:COMPATIBLE_WITH]-(b:Product {name: $product_b})
RETURN count(r) > 0 AS compatible
"""

# 模板 2：查询与某商品直接兼容的所有商品
_TEMPLATE_COMPATIBLE_PRODUCTS = """
MATCH (p:Product {name: $product_name})-[:COMPATIBLE_WITH]-(other:Product)
RETURN other.name AS product
ORDER BY product
"""

# 模板 3：查询某商品支持的所有协议
_TEMPLATE_PROTOCOLS = """
MATCH (p:Product {name: $product_name})-[:SUPPORTS]->(prot:Protocol)
RETURN prot.name AS protocol
ORDER BY protocol
"""

# 模板 4：查询某商品的保修政策
_TEMPLATE_WARRANTY = """
MATCH (p:Product {name: $product_name})-[:COVERED_BY]->(pol:Policy)
RETURN pol.name AS policy_name,
       pol.duration AS duration,
       pol.description AS description
"""


class GraphService:
    """只通过固定 Cypher 模板与参数访问 Neo4j。"""

    def __init__(self, driver: Driver) -> None:
        self._driver = driver

    # ------------------------------------------------------------------
    # 查询 1: 兼容性
    # ------------------------------------------------------------------
    def check_compatibility(self, product_a: str, product_b: str) -> bool:
        """返回 product_a 与 product_b 是否直接兼容。"""
        _validate_product(product_a)
        _validate_product(product_b)

        record = self._run_template(
            _TEMPLATE_COMPATIBILITY,
            {"product_a": product_a, "product_b": product_b},
        )
        return record["compatible"] if record else False

    def get_compatible_products(self, product_name: str) -> list[str]:
        """返回与 product_name 直接兼容的商品名称列表。"""
        _validate_product(product_name)

        records = self._run_template(
            _TEMPLATE_COMPATIBLE_PRODUCTS,
            {"product_name": product_name},
            expect_many=True,
        )
        return [record["product"] for record in records]

    # ------------------------------------------------------------------
    # 查询 2: 协议
    # ------------------------------------------------------------------
    def get_protocols(self, product_name: str) -> list[str]:
        """返回 product_name 支持的协议名称列表。"""
        _validate_product(product_name)

        records = self._run_template(
            _TEMPLATE_PROTOCOLS,
            {"product_name": product_name},
            expect_many=True,
        )
        return [r["protocol"] for r in records]

    # ------------------------------------------------------------------
    # 查询 3: 保修政策
    # ------------------------------------------------------------------
    def get_warranty(self, product_name: str) -> dict | None:
        """返回 product_name 的保修政策信息，若无则返回 None。"""
        _validate_product(product_name)

        record = self._run_template(
            _TEMPLATE_WARRANTY,
            {"product_name": product_name},
        )
        if record is None:
            return None
        return {
            "policy_name": record["policy_name"],
            "duration": record["duration"],
            "description": record["description"],
        }

    # ------------------------------------------------------------------
    # 内部：执行固定模板
    # ------------------------------------------------------------------
    def _run_template(
        self,
        cypher: str,
        params: dict,
        *,
        expect_many: bool = False,
    ):
        """执行一条参数化 Cypher 模板并返回结果。"""
        with self._driver.session() as session:
            result = session.run(cypher, params)
            if expect_many:
                return list(result)
            single = result.single()
            return single
