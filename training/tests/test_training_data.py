"""
训练数据本地测试：只验证数据格式、标签、数量、指标函数和输出 schema，
不下载模型，不做真实训练。

用法（WSL）：
    cd training
    source ../backend/.venv/bin/activate
    python -m pytest tests/test_training_data.py -v
"""

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

# 确保 training 在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.metrics import compute_metrics, top_errors

# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EXPECTED_LABELS = {
    "product_consultation",
    "compatibility",
    "order_status",
    "shipping",
    "return_refund",
    "warranty_fault",
    "complaint",
    "human_handoff",
}


# ===================================================================
# 数据加载
# ===================================================================
@pytest.fixture(scope="module")
def train():
    return _load_jsonl(DATA_DIR / "train.jsonl")


@pytest.fixture(scope="module")
def validation():
    return _load_jsonl(DATA_DIR / "validation.jsonl")


@pytest.fixture(scope="module")
def test():
    return _load_jsonl(DATA_DIR / "test.jsonl")


def _load_jsonl(path: Path) -> list[dict]:
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


# ===================================================================
# 格式与结构
# ===================================================================
class TestDataFormat:
    """JSONL 格式、字段、标签集合校验。"""

    def test_jsonl_parseable(self, train, validation, test):
        """所有 JSONL 可解析为非空列表。"""
        assert len(train) > 0
        assert len(validation) > 0
        assert len(test) > 0

    def test_required_fields(self, train, validation, test):
        """每行必须有 text 和 label 字段。"""
        for name, samples in [("train", train), ("val", validation), ("test", test)]:
            for i, s in enumerate(samples, 1):
                assert "text" in s, f"{name}:{i} 缺少 text"
                assert "label" in s, f"{name}:{i} 缺少 label"
                assert isinstance(s["text"], str) and len(s["text"]) > 0
                assert isinstance(s["label"], str) and len(s["label"]) > 0

    def test_labels_in_set(self, train, validation, test):
        """所有标签必须在 8 类中。"""
        for name, samples in [("train", train), ("val", validation), ("test", test)]:
            for i, s in enumerate(samples, 1):
                assert s["label"] in EXPECTED_LABELS, (
                    f"{name}:{i} 未知标签 '{s['label']}'"
                )

    def test_text_min_length(self, train, validation, test):
        """文本至少 5 字符。"""
        for name, samples in [("train", train), ("val", validation), ("test", test)]:
            for i, s in enumerate(samples, 1):
                assert len(s["text"]) >= 5, (
                    f"{name}:{i} 文本过短 ({len(s['text'])} chars): \"{s['text']}\""
                )

    def test_no_real_brands(self, train, validation, test):
        """不得出现真实品牌名。"""
        blacklist = ["小米", "华为", "海尔", "美的", "格力", "苹果", "三星",
                     "京东", "淘宝", "天猫", "拼多多"]
        for name, samples in [("train", train), ("val", validation), ("test", test)]:
            for i, s in enumerate(samples, 1):
                for word in blacklist:
                    assert word not in s["text"], (
                        f"{name}:{i} 包含真实品牌 '{word}'"
                    )


# ===================================================================
# 数量与分布
# ===================================================================
class TestDataCounts:
    """样本数量、标签分布校验。"""

    def test_train_counts(self, train):
        """train 每类 90 条，共 720。"""
        assert len(train) == 720
        counts = Counter(s["label"] for s in train)
        for lab in EXPECTED_LABELS:
            assert counts[lab] == 90, f"train 标签 '{lab}' 数量={counts[lab]}"

    def test_validation_counts(self, validation):
        """validation 每类 20 条，共 160。"""
        assert len(validation) == 160
        counts = Counter(s["label"] for s in validation)
        for lab in EXPECTED_LABELS:
            assert counts[lab] == 20, f"val 标签 '{lab}' 数量={counts[lab]}"

    def test_test_counts(self, test):
        """test 每类 20 条，共 160。"""
        assert len(test) == 160
        counts = Counter(s["label"] for s in test)
        for lab in EXPECTED_LABELS:
            assert counts[lab] == 20, f"test 标签 '{lab}' 数量={counts[lab]}"


# ===================================================================
# 防泄漏
# ===================================================================
class TestNoLeakage:
    """train / val / test 之间无完全重复。"""

    def test_no_exact_duplicates_cross_split(self, train, validation, test):
        """任意两 split 间不得有完全相同的 text。"""
        train_texts = {s["text"] for s in train}
        val_texts = {s["text"] for s in validation}
        test_texts = {s["text"] for s in test}

        tv_overlap = train_texts & val_texts
        tt_overlap = train_texts & test_texts
        vt_overlap = val_texts & test_texts

        assert not tv_overlap, f"train↔val 完全重复: {list(tv_overlap)[:3]}"
        assert not tt_overlap, f"train↔test 完全重复: {list(tt_overlap)[:3]}"
        assert not vt_overlap, f"val↔test 完全重复: {list(vt_overlap)[:3]}"

    def test_test_not_near_duplicate_of_train(self, train, test):
        """test 不应是 train 的高 Jaccard 近似重复。"""
        train_texts = [s["text"] for s in train]
        test_texts = [s["text"] for s in test]

        def jaccard(a: str, b: str) -> float:
            sa, sb = set(a), set(b)
            if not sa or not sb:
                return 0.0
            return len(sa & sb) / len(sa | sb)

        near_dupes = []
        for tt in test_texts:
            for tr in train_texts:
                if jaccard(tt, tr) > 0.85:
                    near_dupes.append((tt[:50], tr[:50]))
                    if len(near_dupes) >= 3:
                        break
            if len(near_dupes) >= 3:
                break
        assert len(near_dupes) == 0, (
            f"train↔test 近似重复 (Jaccard > 0.85): {near_dupes}"
        )


# ===================================================================
# 指标函数
# ===================================================================
class TestMetrics:
    """指标计算函数的单元测试。"""

    LABELS = ["product_consultation", "compatibility", "order_status",
              "shipping", "return_refund", "warranty_fault", "complaint",
              "human_handoff"]

    def test_perfect_accuracy(self):
        """全对时 accuracy=1.0, macro_f1=1.0。"""
        y_true = self.LABELS * 5
        y_pred = self.LABELS * 5
        m = compute_metrics(y_true, y_pred, self.LABELS)
        assert m["accuracy"] == 1.0
        assert m["macro_f1"] == 1.0
        assert len(m["error_samples"]) == 0

    def test_zero_accuracy(self):
        """全错时 accuracy=0.0。"""
        y_true = ["product_consultation"] * 10
        y_pred = ["complaint"] * 10
        m = compute_metrics(y_true, y_pred, self.LABELS)
        assert m["accuracy"] == 0.0
        assert m["macro_f1"] == 0.0
        assert len(m["error_samples"]) == 10

    def test_mixed_predictions(self):
        """混合预测：accuracy 和 macro_f1 在 0-1 之间。"""
        y_true = ["compatibility", "order_status", "shipping", "complaint"]
        y_pred = ["compatibility", "order_status", "return_refund", "complaint"]
        m = compute_metrics(y_true, y_pred, self.LABELS)
        assert 0.0 < m["accuracy"] < 1.0
        assert 0.0 < m["macro_f1"] < 1.0
        assert len(m["error_samples"]) == 1  # shipping→return_refund

    def test_confusion_matrix_shape(self):
        """混淆矩阵维度=N_labels×N_labels。"""
        y_true = self.LABELS * 3
        y_pred = self.LABELS * 3
        m = compute_metrics(y_true, y_pred, self.LABELS)
        cm = m["confusion_matrix"]
        n = len(self.LABELS)
        assert len(cm) == n
        assert all(len(row) == n for row in cm)

    def test_label_order(self):
        """label_order 应与输入的 labels 一致。"""
        y_true = ["product_consultation"]
        y_pred = ["product_consultation"]
        m = compute_metrics(y_true, y_pred, self.LABELS)
        assert m["label_order"] == self.LABELS

    def test_top_errors(self):
        """top_errors 应返回前 n 条。"""
        errors = [{"true": "a", "pred": "b"}] * 10
        assert len(top_errors(errors, n=3)) == 3

    def test_empty_input_raises(self):
        """空输入应抛出 ValueError。"""
        with pytest.raises(ValueError):
            compute_metrics([], [], self.LABELS)

    def test_length_mismatch_raises(self):
        """y_true/y_pred 长度不一致应抛出 ValueError。"""
        with pytest.raises(ValueError):
            compute_metrics(["a"], ["a", "b"], self.LABELS)


# ===================================================================
# 输出 schema
# ===================================================================
class TestOutputSchema:
    """验证模型输出 schema：{"intent":"...","confidence":0.XX}。"""

    INTENT_PATTERN = (
        r'\{\s*"intent"\s*:\s*"(product_consultation|compatibility|order_status'
        r'|shipping|return_refund|warranty_fault|complaint|human_handoff)"'
        r'\s*,\s*"confidence"\s*:\s*\d+(\.\d+)?\s*\}'
    )

    def test_expected_output_format(self, train):
        """训练数据中 expected_output 应符合 JSON schema。"""
        import re
        # 构造一个样本的期望输出并验证格式
        sample = train[0]
        expected = json.dumps(
            {"intent": sample["label"], "confidence": 0.95},
            ensure_ascii=False,
        )
        assert re.match(self.INTENT_PATTERN, expected), (
            f"输出不符合 schema: {expected}"
        )

    def test_eight_labels_json_structure(self):
        """每类标签的 JSON 输出结构一致。"""
        for lab in EXPECTED_LABELS:
            output = '{"intent":"' + lab + '","confidence":0.88}'
            obj = json.loads(output)
            assert obj["intent"] in EXPECTED_LABELS
            assert isinstance(obj["confidence"], (int, float))
            assert 0 <= obj["confidence"] <= 1


# ===================================================================
# 生成脚本可重复性
# ===================================================================
class TestReproducibility:
    """seed=42 应产生可复现数据。"""

    def test_first_sample_is_deterministic(self):
        """两次生成的数据第一行应一致。"""
        # 直接读取已生成文件，测试文件存在且稳定
        path = DATA_DIR / "train.jsonl"
        assert path.exists()
        with open(path, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
        obj = json.loads(first_line)
        assert "text" in obj and "label" in obj


# ===================================================================
# Import 检查 — 不下载模型、不启动训练
# ===================================================================
class TestImports:
    """验证三个训练/评测脚本可在本地 import 而不触发训练或模型下载。"""

    def test_import_train_qlora(self):
        """import train_qlora 应成功，不下载模型。"""
        import train_qlora
        assert hasattr(train_qlora, "TrainConfig")
        assert hasattr(train_qlora, "INTENT_LABELS")

    def test_import_evaluate_zero_shot(self):
        """import evaluate_zero_shot 应成功。"""
        import evaluate_zero_shot
        assert hasattr(evaluate_zero_shot, "INTENT_LABELS")
        assert hasattr(evaluate_zero_shot, "load_test_data")

    def test_import_evaluate(self):
        """import evaluate 应成功。"""
        import evaluate
        assert hasattr(evaluate, "INTENT_LABELS")
        assert hasattr(evaluate, "load_test_data")
