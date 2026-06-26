"""
数据校验脚本：验证 JSONL 数据集的完整性、标签正确性、无泄漏。

用法:
    python scripts/validate_data.py

返回 0 则全部通过，非 0 则有错误。
"""

import json
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
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

EXPECTED_COUNTS = {
    "train": {lab: 90 for lab in EXPECTED_LABELS},
    "validation": {lab: 20 for lab in EXPECTED_LABELS},
    "test": {lab: 20 for lab in EXPECTED_LABELS},
}

# 真实公司/平台名黑名单（出现即告警）
BLACKLIST_WORDS = [
    "小米", "华为", "海尔", "美的", "格力", "苹果", "三星",
    "京东", "淘宝", "天猫", "拼多多", "amazon", "Amazon",
    "海康威视", "大华", "萤石", "TP-LINK", "tplink",
    "中国移动", "中国联通", "中国电信",
]

# 真实 PII 模式检查（简单规则）
import re

PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
ID_PATTERN = re.compile(r"\d{17}[\dXx]|\d{15}")


def load_jsonl(path: Path) -> list[dict]:
    """加载 JSONL 文件。"""
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[ERROR] {path.name}:{i} — JSON 解析失败: {e}")
                sys.exit(1)
            samples.append(obj)
    return samples


def validate_split(name: str, samples: list[dict]) -> list[str]:
    """验证单份数据。返回错误列表。"""
    errors: list[str] = []

    # 1. 必需字段
    for i, s in enumerate(samples, 1):
        if "text" not in s:
            errors.append(f"{name}:{i} 缺少 'text' 字段")
        if "label" not in s:
            errors.append(f"{name}:{i} 缺少 'label' 字段")
        if not isinstance(s.get("text"), str) or not s["text"].strip():
            errors.append(f"{name}:{i} 'text' 为空")
        if s.get("label") not in EXPECTED_LABELS:
            errors.append(f"{name}:{i} 未知标签 '{s.get('label')}'")

    # 2. 标签分布
    label_counts = Counter(s.get("label") for s in samples)
    for lab in EXPECTED_LABELS:
        actual = label_counts.get(lab, 0)
        expected = EXPECTED_COUNTS[name][lab]
        if actual != expected:
            errors.append(
                f"{name} 标签 '{lab}' 数量: {actual} (期望 {expected})"
            )

    # 3. 黑名单词
    for i, s in enumerate(samples, 1):
        text = s.get("text", "")
        for word in BLACKLIST_WORDS:
            if word.lower() in text.lower():
                errors.append(
                    f"{name}:{i} 包含真实公司/平台名 '{word}' → \"{text[:60]}…\""
                )

    # 4. PII
    for i, s in enumerate(samples, 1):
        text = s.get("text", "")
        if PHONE_PATTERN.search(text):
            errors.append(
                f"{name}:{i} 疑似包含手机号 → \"{text[:60]}…\""
            )
        if ID_PATTERN.search(text):
            errors.append(
                f"{name}:{i} 疑似包含身份证号 → \"{text[:60]}…\""
            )

    # 5. 文本长度合理性
    for i, s in enumerate(samples, 1):
        text = s.get("text", "")
        if len(text) < 5:
            errors.append(f"{name}:{i} 文本过短 ({len(text)} chars)")
        if len(text) > 200:
            errors.append(f"{name}:{i} 文本过长 ({len(text)} chars)")

    return errors


def validate_no_leakage(
    train: list[dict], validation: list[dict], test: list[dict]
) -> list[str]:
    """检查 train/val/test 之间是否存在完全相同的文本。"""
    errors: list[str] = []
    train_texts = {s["text"] for s in train}
    val_texts = {s["text"] for s in validation}
    test_texts = {s["text"] for s in test}

    train_val_overlap = train_texts & val_texts
    train_test_overlap = train_texts & test_texts
    val_test_overlap = val_texts & test_texts

    if train_val_overlap:
        errors.append(
            f"train↔validation 完全重复 {len(train_val_overlap)} 条: "
            + ", ".join(list(train_val_overlap)[:3])
        )
    if train_test_overlap:
        errors.append(
            f"train↔test 完全重复 {len(train_test_overlap)} 条: "
            + ", ".join(list(train_test_overlap)[:3])
        )
    if val_test_overlap:
        errors.append(
            f"validation↔test 完全重复 {len(val_test_overlap)} 条: "
            + ", ".join(list(val_test_overlap)[:3])
        )

    # 近似重复检查：Jaccard 相似度 > 0.8
    def tokenize(s: str) -> set[str]:
        return set(s)

    threshold = 0.8
    near_dupes = []
    for t_text in test_texts:
        t_tokens = tokenize(t_text)
        for tr_text in train_texts:
            tr_tokens = tokenize(tr_text)
            if not t_tokens or not tr_tokens:
                continue
            jaccard = len(t_tokens & tr_tokens) / len(t_tokens | tr_tokens)
            if jaccard > threshold:
                near_dupes.append((t_text, tr_text, jaccard))
                if len(near_dupes) >= 5:
                    break
        if len(near_dupes) >= 5:
            break

    if near_dupes:
        errors.append(
            f"发现 train↔test 近似重复 (Jaccard > {threshold}):\n"
            + "\n".join(
                f"  test:  {t[:60]}…\n  train: {r[:60]}…\n  sim={s:.2f}"
                for t, r, s in near_dupes
            )
        )

    return errors


def main() -> int:
    data_dir = Path(__file__).resolve().parent.parent / "data"
    all_errors: list[str] = []

    # 加载
    train = load_jsonl(data_dir / "train.jsonl")
    val = load_jsonl(data_dir / "validation.jsonl")
    test = load_jsonl(data_dir / "test.jsonl")

    # 分片校验
    for name, samples in [("train", train), ("validation", val), ("test", test)]:
        errs = validate_split(name, samples)
        all_errors.extend(errs)

    # 泄漏检查
    leak_errs = validate_no_leakage(train, val, test)
    all_errors.extend(leak_errs)

    # 报告
    if all_errors:
        print(f"\n❌ 校验发现 {len(all_errors)} 个错误:\n")
        for e in all_errors:
            print(f"  - {e}")
        return 1
    else:
        print(f"✅ 全部校验通过")
        print(f"   train:      {len(train)} samples")
        print(f"   validation: {len(val)} samples")
        print(f"   test:       {len(test)} samples")
        print(f"   标签分布: {Counter(s['label'] for s in train)}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
