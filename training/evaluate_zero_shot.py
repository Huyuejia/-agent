"""
Zero-shot 基线评测：使用 Qwen2.5-1.5B-Instruct 在 test.jsonl 上做零样本分类。

不在此环境运行 — 仅供在云 GPU 实例上执行。

用法（云 GPU 上）:
    python evaluate_zero_shot.py \
        --model_name Qwen/Qwen2.5-1.5B-Instruct \
        --test_data data/test.jsonl \
        --output_dir ./reports
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 将 training 目录加入 sys.path，以便导入 scripts.metrics
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.metrics import compute_metrics, top_errors

# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "你是一个智能家居客服意图分类器。根据用户输入，判断意图并返回 JSON。\n"
    "意图类别: product_consultation, compatibility, order_status, shipping, "
    "return_refund, warranty_fault, complaint, human_handoff\n"
    "只返回 JSON，不要添加任何解释。\n"
    '示例输出: {"intent":"compatibility","confidence":0.92}'
)

INTENT_LABELS = [
    "product_consultation",
    "compatibility",
    "order_status",
    "shipping",
    "return_refund",
    "warranty_fault",
    "complaint",
    "human_handoff",
]


def load_test_data(path: str) -> list[dict]:
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def parse_response(text: str) -> tuple[str, float]:
    """从模型输出中解析 intent 和 confidence。"""
    # 尝试直接 JSON 解析
    try:
        obj = json.loads(text.strip())
        intent = obj.get("intent", "").strip()
        conf = float(obj.get("confidence", 0.0))
        if intent in INTENT_LABELS:
            return intent, conf
    except (json.JSONDecodeError, ValueError):
        pass

    # 尝试从文本中提取 JSON
    import re

    json_match = re.search(r'\{[^}]+\}', text)
    if json_match:
        try:
            obj = json.loads(json_match.group())
            intent = obj.get("intent", "").strip()
            conf = float(obj.get("confidence", 0.0))
            if intent in INTENT_LABELS:
                return intent, conf
        except (json.JSONDecodeError, ValueError):
            pass

    return "unknown", 0.0


def run_zero_shot(model, tokenizer, samples: list[dict]) -> list[dict]:
    """逐条推理，返回预测结果。"""
    import torch
    from tqdm import tqdm

    results = []
    for sample in tqdm(samples, desc="zero-shot"):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": sample["text"]},
        ]
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(text, return_tensors="pt").to(model.device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=64,
                temperature=0.1,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
            )

        response = tokenizer.decode(
            outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        )
        intent, confidence = parse_response(response)
        results.append({
            "text": sample["text"],
            "true_label": sample["label"],
            "pred_label": intent if intent != "unknown" else "human_handoff",
            "confidence": confidence,
            "raw_output": response,
        })
    return results


def main():
    parser = argparse.ArgumentParser(description="Zero-shot 基线评测")
    parser.add_argument("--model_name", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--test_data", default="data/test.jsonl")
    parser.add_argument("--output_dir", default="./reports")
    parser.add_argument("--max_samples", type=int, default=0,
                        help="限制评测样本数（0=全部）")
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"[INFO] 加载模型: {args.model_name}")
    tokenizer = AutoTokenizer.from_pretrained(
        args.model_name, trust_remote_code=True, padding_side="left"
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.model_name,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
    )

    samples = load_test_data(args.test_data)
    if args.max_samples > 0:
        samples = samples[: args.max_samples]

    print(f"[INFO] 测试样本: {len(samples)}")
    results = run_zero_shot(model, tokenizer, samples)

    y_true = [r["true_label"] for r in results]
    y_pred = [r["pred_label"] for r in results]
    metrics = compute_metrics(y_true, y_pred, INTENT_LABELS)

    # 保存报告
    os.makedirs(args.output_dir, exist_ok=True)
    report_path = os.path.join(args.output_dir, "zero_shot_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": args.model_name,
                "num_samples": len(samples),
                **metrics,
                "top_errors": top_errors(metrics["error_samples"], n=5),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n[OK] Zero-shot 报告保存至 {report_path}")
    print(f"     Accuracy:  {metrics['accuracy']}")
    print(f"     Macro-F1:  {metrics['macro_f1']}")
    print(f"     错误样本:  {len(metrics['error_samples'])}/{len(samples)}")


if __name__ == "__main__":
    main()
