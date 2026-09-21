"""Tokenizer（JiebaTokenizer）单元测试。

验证 SKU 与中文政策词不会被错误丢失，且索引/查询分词一致。
"""

from app.retrieval import JiebaTokenizer, Tokenizer


def _tokenize(text):
    return JiebaTokenizer().tokenize(text)


def test_tokenizes_chinese_policy_words():
    tokens = _tokenize("退货政策")
    assert "退货" in tokens
    assert "政策" in tokens


def test_sku_is_not_lost():
    tokens = _tokenize("Cam-A1 多少钱")
    assert "Cam-A1" in tokens


def test_sku_list_not_lost():
    tokens = _tokenize("Hub-Z1 Sensor-T1 Lock-D1")
    assert "Hub-Z1" in tokens
    assert "Sensor-T1" in tokens
    assert "Lock-D1" in tokens


def test_domain_words_preserved():
    tokens = _tokenize("Zigbee WiFi 蓝牙 网关 保修")
    for word in ("Zigbee", "WiFi", "蓝牙", "网关", "保修"):
        assert word in tokens


def test_empty_text_returns_empty():
    assert _tokenize("") == []
    assert _tokenize("   ") == []


def test_no_whitespace_tokens():
    tokens = _tokenize("智家 智能摄像头 保修")
    assert all(token.strip() for token in tokens)


def test_implements_tokenizer_protocol():
    assert isinstance(JiebaTokenizer(), Tokenizer)
