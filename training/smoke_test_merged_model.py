"""Smoke-test a standalone merged intent model before ONNX export."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from training.intent_classifier import SYSTEM_PROMPT, parse_prediction


DEFAULT_TEXTS = (
    "我要退货",
    "客服态度太差，我要投诉",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load a merged model once and run intent smoke tests.",
    )
    parser.add_argument("--model-path", required=True)
    parser.add_argument(
        "--text",
        action="append",
        dest="texts",
        help="Input text to classify; repeat for multiple samples.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_path = Path(args.model_path)
    if not model_path.is_dir():
        raise FileNotFoundError(f"Merged model directory not found: {model_path}")

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")

    started_at = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        local_files_only=True,
        trust_remote_code=True,
        padding_side="left",
        # Transformers 4.57.x can mis-detect local Qwen2 tokenizers as
        # Mistral-family tokenizers. Keep the Qwen2 pre-tokenizer unchanged.
        fix_mistral_regex=False,
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.float16,
        device_map={"": "cuda:0"},
        low_cpu_mem_usage=True,
    )
    model.eval()
    print(
        f"[READY] merged_model_load_seconds={time.perf_counter() - started_at:.3f}",
        flush=True,
    )

    pad_token_id = tokenizer.pad_token_id
    if pad_token_id is None:
        pad_token_id = tokenizer.eos_token_id

    for text in args.texts or DEFAULT_TEXTS:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        prompt = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

        torch.cuda.synchronize()
        predicted_at = time.perf_counter()
        with torch.inference_mode():
            outputs = model.generate(
                **inputs,
                max_new_tokens=64,
                do_sample=False,
                pad_token_id=pad_token_id,
            )
        torch.cuda.synchronize()
        latency_ms = (time.perf_counter() - predicted_at) * 1000

        generated_tokens = outputs[0][inputs["input_ids"].shape[1] :]
        raw_output = tokenizer.decode(
            generated_tokens,
            skip_special_tokens=True,
        )
        intent, confidence = parse_prediction(raw_output)
        print(
            json.dumps(
                {
                    "text": text,
                    "intent": intent,
                    "confidence": confidence,
                    "latency_ms": round(latency_ms, 2),
                    "raw_output": raw_output,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
