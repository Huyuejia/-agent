"""
指标计算工具：accuracy, macro-F1, 混淆矩阵, 错误样例。

完全不依赖模型 — 只接收 y_true / y_pred 列表。
所有指标从实际数据计算，绝不编造。
"""

from typing import Any

import numpy as np


def compute_metrics(
    y_true: list[str],
    y_pred: list[str],
    labels: list[str],
) -> dict[str, Any]:
    """
    计算分类指标。

    Args:
        y_true: 真实标签列表
        y_pred: 预测标签列表
        labels: 全部标签（用于构建混淆矩阵行/列）

    Returns:
        {
            "accuracy": float,
            "macro_f1": float,
            "per_class_f1": {label: float, ...},
            "confusion_matrix": list[list[int]],
            "label_order": list[str],
            "error_samples": [{"text": str, "true": str, "pred": str}, ...],
        }
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"y_true ({len(y_true)}) 与 y_pred ({len(y_pred)}) 长度不一致"
        )
    if len(y_true) == 0:
        raise ValueError("输入不能为空")

    label_to_idx = {lab: i for i, lab in enumerate(labels)}
    n_labels = len(labels)

    # ---- 混淆矩阵 ----
    cm = np.zeros((n_labels, n_labels), dtype=int)
    correct = 0
    error_samples: list[dict[str, str]] = []

    for i, (t, p) in enumerate(zip(y_true, y_pred)):
        ti = label_to_idx.get(t)
        pi = label_to_idx.get(p)
        if ti is not None and pi is not None:
            cm[ti][pi] += 1
        if t == p:
            correct += 1
        else:
            error_samples.append({"index": i, "true": t, "pred": p})

    accuracy = correct / len(y_true)

    # ---- per-class F1 ----
    per_class_f1: dict[str, float] = {}
    f1_scores: list[float] = []

    for label in labels:
        idx = label_to_idx[label]
        tp = cm[idx][idx]
        fp = cm[:, idx].sum() - tp
        fn = cm[idx, :].sum() - tp
        if tp + fp + fn == 0:
            f1 = 0.0
        else:
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (
                2 * precision * recall / (precision + recall)
                if (precision + recall) > 0
                else 0.0
            )
        per_class_f1[label] = round(f1, 4)
        f1_scores.append(f1)

    macro_f1 = round(float(np.mean(f1_scores)), 4)

    return {
        "accuracy": round(accuracy, 4),
        "macro_f1": macro_f1,
        "per_class_f1": per_class_f1,
        "confusion_matrix": cm.tolist(),
        "label_order": labels,
        "error_samples": error_samples,  # 全量；调用方按需截断
    }


def top_errors(
    error_samples: list[dict[str, Any]], n: int = 5
) -> list[dict[str, Any]]:
    """返回前 n 条错误样例（不做排序，保留原始顺序）。"""
    return error_samples[:n]
