"""
Adapter 评测脚本：加载 QLoRA Adapter 在 test.jsonl 上评测。

不在此环境运行 — 仅供在云 GPU 实例上执行。

用法（云 GPU 上）:
    python evaluate.py \
        --base_model Qwen/Qwen2.5-1.5B-Instruct \
        --adapter_path ./checkpoints/lora-adapter/final \
        --test_data data/test.jsonl \
        --output_dir ./reports
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

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
    try:
        obj = json.loads(text.strip())
        intent = obj.get("intent", "").strip()
        conf = float(obj.get("confidence", 0.0))
        if intent in INTENT_LABELS:
            return intent, conf
    except (json.JSONDecodeError, ValueError):
        pass

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


def run_eval(model, tokenizer, samples: list[dict]) -> list[dict]:
    """逐条推理。"""
    import torch
    from tqdm import tqdm

    results = []
    for sample in tqdm(samples, desc="eval"):
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
    parser = argparse.ArgumentParser(description="Adapter 评测")
    parser.add_argument("--base_model", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--adapter_path", required=True)
    parser.add_argument("--test_data", default="data/test.jsonl")
    parser.add_argument("--output_dir", default="./reports")
    parser.add_argument("--max_samples", type=int, default=0)
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    print(f"[INFO] 加载基座模型: {args.base_model}")
    tokenizer = AutoTokenizer.from_pretrained(
        args.base_model, trust_remote_code=True, padding_side="left"
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    base_model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
    )

    print(f"[INFO] 加载 Adapter: {args.adapter_path}")
    model = PeftModel.from_pretrained(base_model, args.adapter_path)
    model = model.merge_and_unload()

    samples = load_test_data(args.test_data)
    if args.max_samples > 0:
        samples = samples[: args.max_samples]

    print(f"[INFO] 测试样本: {len(samples)}")
    results = run_eval(model, tokenizer, samples)

    y_true = [r["true_label"] for r in results]
    y_pred = [r["pred_label"] for r in results]
    metrics = compute_metrics(y_true, y_pred, INTENT_LABELS)

    # 保存报告
    os.makedirs(args.output_dir, exist_ok=True)
    report_path = os.path.join(args.output_dir, "adapter_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "base_model": args.base_model,
                "adapter_path": args.adapter_path,
                "num_samples": len(samples),
                **metrics,
                "top_errors": top_errors(metrics["error_samples"], n=5),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n[OK] Adapter 报告保存至 {report_path}")
    print(f"     Accuracy:  {metrics['accuracy']}")
    print(f"     Macro-F1:  {metrics['macro_f1']}")
    print(f"     错误样本:  {len(metrics['error_samples'])}/{len(samples)}")


if __name__ == "__main__":
    main()
