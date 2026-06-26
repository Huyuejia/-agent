"""
Qwen2.5-1.5B-Instruct 4-bit QLoRA 意图分类微调脚本。

不在此环境运行 — 仅供在云 GPU 实例上执行。
若 1.5B 显存不足，回退至 Qwen2.5-0.5B-Instruct。

用法（云 GPU 上）:
    python train_qlora.py \
        --model_name Qwen/Qwen2.5-1.5B-Instruct \
        --train_data data/train.jsonl \
        --val_data data/validation.jsonl \
        --output_dir ./checkpoints/lora-adapter \
        --epochs 3
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# torch 可能未安装（本地测试不下载模型）；惰性导入以允许 import 检查
try:
    import torch
except ImportError:  # pragma: no cover — 本地测试不会走此分支
    torch = None  # type: ignore

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass
class TrainConfig:
    model_name: str = "Qwen/Qwen2.5-1.5B-Instruct"
    fallback_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    train_data: str = "data/train.jsonl"
    val_data: str = "data/validation.jsonl"
    output_dir: str = "./checkpoints/lora-adapter"
    epochs: int = 3
    batch_size: int = 4
    grad_accum_steps: int = 4
    learning_rate: float = 2e-4
    max_length: int = 256
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    # 4-bit NF4
    bnb_4bit_compute_dtype: str = "bfloat16"
    bnb_4bit_quant_type: str = "nf4"
    # LoRA target modules
    lora_target_modules: tuple[str, ...] = field(default_factory=lambda: (
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ))
    save_steps: int = 200
    logging_steps: int = 50
    warmup_ratio: float = 0.1
    weight_decay: float = 0.01
    seed: int = 42


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
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

SYSTEM_PROMPT = (
    "你是一个智能家居客服意图分类器。根据用户输入，判断意图并返回 JSON。\n"
    "意图类别: product_consultation, compatibility, order_status, shipping, "
    "return_refund, warranty_fault, complaint, human_handoff\n"
    "只返回 JSON，不要添加任何解释。\n"
    '示例输出: {"intent":"compatibility","confidence":0.92}'
)


def load_jsonl_samples(path: str) -> list[dict]:
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def format_chatml(sample: dict) -> dict:
    """将样本转为 ChatML 格式（messages + 期望输出 JSON）。"""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": sample["text"]},
        ],
        "expected_output": json.dumps(
            {"intent": sample["label"], "confidence": 0.95},
            ensure_ascii=False,
        ),
    }


# ---------------------------------------------------------------------------
# 模型加载
# ---------------------------------------------------------------------------
def load_model_and_tokenizer(config: TrainConfig):
    """加载 4-bit 量化模型与 tokenizer。显存不足时自动回退到 fallback_model。"""
    import torch
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
    )

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=getattr(torch, config.bnb_4bit_compute_dtype),
        bnb_4bit_quant_type=config.bnb_4bit_quant_type,
        bnb_4bit_use_double_quant=True,
    )

    model_name = config.model_name
    try:
        print(f"[INFO] 加载模型: {model_name}")
        tokenizer = AutoTokenizer.from_pretrained(
            model_name, trust_remote_code=True, padding_side="left"
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
            torch_dtype=torch.bfloat16,
        )
    except (torch.cuda.OutOfMemoryError, RuntimeError) as e:
        if "out of memory" in str(e).lower() and model_name != config.fallback_model:
            print(
                f"[WARN] {config.model_name} 显存不足，回退至 {config.fallback_model}"
            )
            model_name = config.fallback_model
            tokenizer = AutoTokenizer.from_pretrained(
                model_name, trust_remote_code=True, padding_side="left"
            )
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token
            model = AutoModelForCausalLM.from_pretrained(
                model_name,
                quantization_config=bnb_config,
                device_map="auto",
                trust_remote_code=True,
                torch_dtype=torch.bfloat16,
            )
        else:
            raise

    return model, tokenizer, model_name


# ---------------------------------------------------------------------------
# LoRA 配置
# ---------------------------------------------------------------------------
def setup_lora(model, config: TrainConfig):
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    model = prepare_model_for_kbit_training(model)

    peft_config = LoraConfig(
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=list(config.lora_target_modules),
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()
    return model


# ---------------------------------------------------------------------------
# 数据集
# ---------------------------------------------------------------------------
_DatasetBase = torch.utils.data.Dataset if torch is not None else object  # type: ignore[assignment]


class IntentDataset(_DatasetBase):
    def __init__(self, samples: list[dict], tokenizer, max_length: int):
        self.samples = samples
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        sample = self.samples[idx]
        chatml = format_chatml(sample)
        # 构建完整文本：messages + expected output
        text = self.tokenizer.apply_chat_template(
            chatml["messages"], tokenize=False, add_generation_prompt=True
        )
        text += chatml["expected_output"] + self.tokenizer.eos_token

        encoding = self.tokenizer(
            text,
            truncation=True,
            max_length=self.max_length,
            padding=False,
        )
        # DataCollatorForLanguageModeling 会统一 padding 并生成 labels。
        return {
            "input_ids": encoding["input_ids"],
            "attention_mask": encoding["attention_mask"],
        }


# ---------------------------------------------------------------------------
# 训练
# ---------------------------------------------------------------------------
def train(config: TrainConfig):
    import torch
    from transformers import TrainingArguments, Trainer, DataCollatorForLanguageModeling

    # 数据
    train_samples = load_jsonl_samples(config.train_data)
    val_samples = load_jsonl_samples(config.val_data)
    print(f"[INFO] train={len(train_samples)}, val={len(val_samples)}")

    # 模型
    model, tokenizer, actual_model = load_model_and_tokenizer(config)
    model = setup_lora(model, config)

    # tokenizer
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # dataset
    train_ds = IntentDataset(train_samples, tokenizer, config.max_length)
    val_ds = IntentDataset(val_samples, tokenizer, config.max_length)

    # data collator
    data_collator = DataCollatorForLanguageModeling(
        tokenizer=tokenizer, mlm=False, pad_to_multiple_of=8
    )

    # training args
    training_args = TrainingArguments(
        output_dir=config.output_dir,
        num_train_epochs=config.epochs,
        per_device_train_batch_size=config.batch_size,
        per_device_eval_batch_size=config.batch_size,
        gradient_accumulation_steps=config.grad_accum_steps,
        learning_rate=config.learning_rate,
        warmup_ratio=config.warmup_ratio,
        weight_decay=config.weight_decay,
        logging_steps=config.logging_steps,
        save_steps=config.save_steps,
        eval_strategy="steps",
        eval_steps=config.save_steps,
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        bf16=True,
        seed=config.seed,
        report_to="none",
        dataloader_pin_memory=False,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=data_collator,
    )

    print("[INFO] 开始训练...")
    trainer.train()

    # 保存
    adapter_path = os.path.join(config.output_dir, "final")
    trainer.save_model(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    print(f"[OK] Adapter 保存至 {adapter_path}")

    return adapter_path, actual_model


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QLoRA 意图分类训练")
    parser.add_argument("--model_name", default=TrainConfig.model_name)
    parser.add_argument("--fallback_model", default=TrainConfig.fallback_model)
    parser.add_argument("--train_data", default=TrainConfig.train_data)
    parser.add_argument("--val_data", default=TrainConfig.val_data)
    parser.add_argument("--output_dir", default=TrainConfig.output_dir)
    parser.add_argument("--epochs", type=int, default=TrainConfig.epochs)
    parser.add_argument("--batch_size", type=int, default=TrainConfig.batch_size)
    parser.add_argument("--lr", type=float, default=TrainConfig.learning_rate)
    parser.add_argument("--max_length", type=int, default=TrainConfig.max_length)
    parser.add_argument("--lora_r", type=int, default=TrainConfig.lora_r)
    parser.add_argument("--lora_alpha", type=int, default=TrainConfig.lora_alpha)
    args = parser.parse_args()

    cfg = TrainConfig(
        model_name=args.model_name,
        fallback_model=args.fallback_model,
        train_data=args.train_data,
        val_data=args.val_data,
        output_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        max_length=args.max_length,
        lora_r=args.lora_r,
        lora_alpha=args.lora_alpha,
    )
    train(cfg)
