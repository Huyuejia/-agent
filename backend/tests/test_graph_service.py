"""
GraphService 集成测试 — 需要已播种的 Neo4j 实例。

运行前请确保:
  1. docker compose up -d neo4j
  2. Get-Content graph\\seed.cypher | docker exec -i ciw-neo4j cypher-shell -u neo4j -p demo123456
  3. cd backend && pytest tests/test_graph_service.py -v
"""

import os
import pytest
from neo4j import GraphDatabase

from app.services.graph_service import GraphService, PRODUCT_WHITELIST


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def neo4j_driver():
    """模块级 Neo4j 驱动，所有测试共用。"""
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "demo123456")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    # 连接验证
    driver.verify_connectivity()
    yield driver
    driver.close()


@pytest.fixture(scope="module")
def graph_service(neo4j_driver):
    """模块级 GraphService 实例。"""
    return GraphService(neo4j_driver)


# ---------------------------------------------------------------------------
# 测试 1: 兼容性 — 已知兼容对
# ---------------------------------------------------------------------------

def test_cam_a1_compatible_with_hub_z1(graph_service):
    """Cam-A1 与 Hub-Z1 直接兼容，预期返回 True。"""
    result = graph_service.check_compatibility("Cam-A1", "Hub-Z1")
    assert result is True, (
        f"预期 Cam-A1 与 Hub-Z1 兼容，实际返回 {result}"
    )


# ---------------------------------------------------------------------------
# 测试 2: 兼容性 — 不兼容组合
# ---------------------------------------------------------------------------

def test_cam_a1_not_compatible_with_light_b1(graph_service):
    """Cam-A1 与 Light-B1 无直接 COMPATIBLE_WITH 关系，预期返回 False。"""
    result = graph_service.check_compatibility("Cam-A1", "Light-B1")
    assert result is False, (
        f"预期 Cam-A1 与 Light-B1 不兼容，实际返回 {result}"
    )


def test_plug_p1_not_compatible_with_lock_d1(graph_service):
    """Plug-P1 与 Lock-D1 无直接兼容关系，预期返回 False。"""
    result = graph_service.check_compatibility("Plug-P1", "Lock-D1")
    assert result is False, (
        f"预期 Plug-P1 与 Lock-D1 不兼容，实际返回 {result}"
    )


# ---------------------------------------------------------------------------
# 测试 3: 协议查询
# ---------------------------------------------------------------------------

def test_get_protocols_cam_a1(graph_service):
    """Cam-A1 应支持 WiFi 和 Zigbee 两种协议。"""
    protocols = graph_service.get_protocols("Cam-A1")
    expected = ["WiFi", "Zigbee"]
    assert protocols == expected, (
        f"预期 Cam-A1 协议 {expected}，实际返回 {protocols}"
    )


def test_get_protocols_hub_z1(graph_service):
    """Hub-Z1 应支持 Thread、WiFi、Zigbee 三种协议。"""
    protocols = graph_service.get_protocols("Hub-Z1")
    expected = ["Thread", "WiFi", "Zigbee"]
    assert protocols == expected, (
        f"预期 Hub-Z1 协议 {expected}，实际返回 {protocols}"
    )


def test_get_protocols_lock_d1(graph_service):
    """Lock-D1 应支持 Bluetooth 和 Zigbee。"""
    protocols = graph_service.get_protocols("Lock-D1")
    expected = ["Bluetooth", "Zigbee"]
    assert protocols == expected, (
        f"预期 Lock-D1 协议 {expected}，实际返回 {protocols}"
    )


# ---------------------------------------------------------------------------
# 测试 4: 保修查询
# ---------------------------------------------------------------------------

def test_get_warranty_cam_a1(graph_service):
    """Cam-A1 适用 Standard-1Y 标准保修。"""
    warranty = graph_service.get_warranty("Cam-A1")
    assert warranty is not None, "Cam-A1 应有保修政策"
    assert warranty["policy_name"] == "Standard-1Y"
    assert warranty["duration"] == "1年"


def test_get_warranty_hub_z1(graph_service):
    """Hub-Z1 适用 Premium-3Y 尊享保修。"""
    warranty = graph_service.get_warranty("Hub-Z1")
    assert warranty is not None, "Hub-Z1 应有保修政策"
    assert warranty["policy_name"] == "Premium-3Y"
    assert warranty["duration"] == "3年"


def test_get_warranty_lock_d1(graph_service):
    """Lock-D1 适用 Extended-2Y 延保。"""
    warranty = graph_service.get_warranty("Lock-D1")
    assert warranty is not None, "Lock-D1 应有保修政策"
    assert warranty["policy_name"] == "Extended-2Y"
    assert warranty["duration"] == "2年"


# ---------------------------------------------------------------------------
# 测试 5: 未知商品拒绝 — 不触发任何 Cypher
# ---------------------------------------------------------------------------

def test_unknown_product_raises_on_compatibility(graph_service):
    """未知商品 'Fake-X9' 应在白名单校验阶段抛出 ValueError，不执行 Cypher。"""
    with pytest.raises(ValueError, match="未识别的商品"):
        graph_service.check_compatibility("Fake-X9", "Hub-Z1")


def test_unknown_product_raises_on_protocols(graph_service):
    """空字符串也应在白名单校验阶段抛出 ValueError。"""
    with pytest.raises(ValueError, match="未识别的商品"):
        graph_service.get_protocols("")


def test_unknown_product_raises_on_warranty(graph_service):
    """大小写敏感：'cam-a1' 不在白名单，应被拒绝。"""
    with pytest.raises(ValueError, match="未识别的商品"):
        graph_service.get_warranty("cam-a1")


def test_non_string_input_raises(graph_service):
    """传入非字符串类型也应被拒绝。"""
    with pytest.raises(ValueError, match="未识别的商品"):
        graph_service.check_compatibility(None, "Hub-Z1")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 测试 6: 未知商品绝不到达 session.run（mock driver 证明）
# ---------------------------------------------------------------------------

def test_unknown_product_never_reaches_session_run():
    """用 mock driver 证明：未知商品在 _validate_product 即抛异常，driver.session() 从未被调用。"""
    from unittest.mock import Mock

    mock_driver = Mock()
    # 不给 session 任何实现 — 一旦调用就立即暴露
    mock_driver.session = Mock()

    gs = GraphService(mock_driver)

    # 三个入口一一验证
    with pytest.raises(ValueError, match="未识别的商品"):
        gs.check_compatibility("Fake-X9", "Hub-Z1")
    mock_driver.session.assert_not_called()

    with pytest.raises(ValueError, match="未识别的商品"):
        gs.get_protocols("")
    mock_driver.session.assert_not_called()

    with pytest.raises(ValueError, match="未识别的商品"):
        gs.get_warranty("cam-a1")
    mock_driver.session.assert_not_called()


# ---------------------------------------------------------------------------
# 测试 7: 白名单覆盖 — 确保所有种子数据中的商品都有覆盖
# ---------------------------------------------------------------------------

def test_all_whitelist_products_exist_in_graph(graph_service):
    """白名单中的每个商品至少有一条 SUPPORTS 关系，确认数据完整性。"""
    for product in sorted(PRODUCT_WHITELIST):
        protocols = graph_service.get_protocols(product)
        assert len(protocols) > 0, (
            f"白名单商品 '{product}' 在图谱中应有协议支持，实际为空"
        )
