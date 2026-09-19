"""QueryAnalyzer 确定性行为测试。"""

from app.retrieval import FusionStrategy, MissPolicy, QueryAnalyzer, RetrievalMode


def _analyze(query):
    return QueryAnalyzer().analyze(query)


def test_empty_query_generates_clarification():
    plan = _analyze("   ")
    assert plan.intent == "clarification"
    assert plan.requires_clarification is True
    assert plan.clarification_question
    assert plan.steps == []


def test_order_number_generates_exact_stop():
    plan = _analyze("我的订单 ORD-123456 到哪了")
    assert plan.intent == "exact_lookup"
    assert plan.confidence == 1.0
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.mode is RetrievalMode.EXACT
    assert step.miss_policy is MissPolicy.STOP
    assert step.required is True
    assert "order" in step.filters["entity_types"]
    # 数据库使用标准化值（分隔符被去除）
    assert "ORD123456" in step.filters["entity_values"]
    # 显式标识符不得退化为向量相似
    assert all(s.mode is not RetrievalMode.VECTOR for s in plan.steps)


def test_serial_number_generates_exact():
    plan = _analyze("设备 SN:AB12CD34 无法激活")
    assert plan.intent == "exact_lookup"
    assert plan.steps[0].mode is RetrievalMode.EXACT
    assert "SNAB12CD34" in plan.steps[0].filters["entity_values"]


def test_error_code_generates_exact():
    plan = _analyze("设备报错 E1001")
    assert plan.intent == "exact_lookup"
    assert plan.steps[0].filters["entity_values"] == ["E1001"]


def test_error_expression_without_code_requires_structured_clarification():
    plan = _analyze("设备报错了，怎么处理？")

    assert plan.intent == "error_lookup"
    assert plan.requires_clarification is True
    assert plan.clarification_question == "请提供设备显示的错误码（如 E1001）。"
    assert plan.steps == []


def test_sku_generates_exact_product_lookup():
    plan = _analyze("Cam-A1 多少钱")
    assert plan.intent == "exact_lookup"
    assert plan.steps[0].mode is RetrievalMode.EXACT
    assert "CAM-A1" in plan.steps[0].filters["entity_values"]


def test_sku_adjacent_to_chinese_text_is_detected():
    plan = _analyze("Cam-A1和Hub-Z1能兼容吗？")

    assert plan.intent == "graph_query"
    assert plan.requires_clarification is False
    assert [entity.normalized_value for entity in plan.detected_entities] == [
        "CAM-A1",
        "HUB-Z1",
    ]


def test_mixed_case_sku_normalizes_but_keeps_raw():
    plan = _analyze("cam-a1 多少钱")
    assert plan.intent == "exact_lookup"
    entity = plan.detected_entities[0]
    assert entity.type == "sku"
    assert entity.raw_value == "cam-a1"
    assert entity.normalized_value == "CAM-A1"
    assert plan.steps[0].filters["entity_values"] == ["CAM-A1"]


def test_multiple_identifiers_top_k_not_fixed_to_one():
    plan = _analyze("订单 ORD-123456 里的 Cam-A1 多少钱")
    assert plan.intent == "exact_lookup"
    step = plan.steps[0]
    assert step.mode is RetrievalMode.EXACT
    # top_k 至少等于去重后的标识符数量（订单号 + SKU = 2）
    assert step.top_k == 2
    assert step.filters["entity_types"] == ["order", "sku"]
    assert step.filters["entity_values"] == ["ORD123456", "CAM-A1"]


def test_duplicate_identifiers_deduped_stable_order():
    plan = _analyze("ORD-123456 订单 ORD-123456")
    assert plan.intent == "exact_lookup"
    step = plan.steps[0]
    assert step.top_k == 1
    assert step.filters["entity_values"] == ["ORD123456"]


def test_compatibility_generates_exact_then_graph_with_dependency():
    plan = _analyze("Cam-A1 和 Hub-Z1 兼容吗")
    assert plan.intent == "graph_query"
    assert [s.mode for s in plan.steps] == [RetrievalMode.EXACT, RetrievalMode.GRAPH]
    exact = plan.steps[0]
    graph = plan.steps[1]
    assert exact.step_id == "resolve_entities"
    assert exact.miss_policy is MissPolicy.STOP
    assert set(exact.filters["entity_values"]) == {"CAM-A1", "HUB-Z1"}
    assert graph.step_id == "graph_lookup"
    assert graph.depends_on == ["resolve_entities"]
    # GRAPH 不得携带原始 SKU 作为最终实体 ID
    assert graph.filters == {}
    assert graph.miss_policy is MissPolicy.STOP


def test_relation_with_anaphora_requires_context_not_graph():
    plan = _analyze("这两件商品能兼容吗")
    assert plan.intent == "graph_query"
    assert plan.requires_context is True
    assert plan.requires_clarification is False
    # 只有指代、无实体 → 不生成 GRAPH
    assert all(s.mode is not RetrievalMode.GRAPH for s in plan.steps)


def test_relation_without_entity_or_anaphora_generates_clarification():
    plan = _analyze("什么设备能和网关兼容")
    assert plan.intent == "graph_query"
    assert plan.requires_clarification is True
    assert plan.clarification_question
    assert all(s.mode is not RetrievalMode.GRAPH for s in plan.steps)


def test_document_question_generates_lexical_vector_with_rrf():
    plan = _analyze("退货政策是什么")
    assert plan.intent == "document_qa"
    # 只有 LEXICAL + VECTOR，无第三个 HYBRID 步骤
    assert [s.mode for s in plan.steps] == [
        RetrievalMode.LEXICAL,
        RetrievalMode.VECTOR,
    ]
    assert plan.fusion_strategy is FusionStrategy.RRF
    assert plan.steps[0].step_id == "lexical"
    assert plan.steps[1].step_id == "vector"


def test_analyzer_is_deterministic():
    analyzer = QueryAnalyzer()
    query = "ORD-998877 什么时候发货"
    assert analyzer.analyze(query) == analyzer.analyze(query)
