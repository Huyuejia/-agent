"""Merge a PEFT LoRA adapter into its base model for deployment export."""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge a LoRA adapter and save a standalone FP16 model.",
    )
    parser.add_argument(
        "--base-model",
        default="Qwen/Qwen2.5-1.5B-Instruct",
        help="Hugging Face model ID or local base-model directory.",
    )
    parser.add_argument(
        "--adapter-path",
        required=True,
        help="Local PEFT adapter directory.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="New directory for the merged model and tokenizer.",
    )
    return parser.parse_args()


def ensure_empty_output_dir(output_dir: Path) -> None:
    """Refuse to overwrite an existing artifact accidentally."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(
            f"Output directory is not empty: {output_dir}. "
            "Choose a new path or inspect the existing artifact first."
        )
    output_dir.mkdir(parents=True, exist_ok=True)


def main() -> None:
    args = parse_args()

    adapter_path = Path(args.adapter_path)
    output_dir = Path(args.output_dir)
    if not adapter_path.is_dir():
        raise FileNotFoundError(f"Adapter directory not found: {adapter_path}")
    ensure_empty_output_dir(output_dir)

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA is unavailable. This export path expects an NVIDIA GPU "
            "to keep the merge practical on the current machine."
        )

    print(f"[1/4] Loading tokenizer: {args.base_model}", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(
        args.base_model,
        trust_remote_code=True,
        padding_side="left",
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print("[2/4] Loading FP16 base model on CUDA", flush=True)
    base_model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        trust_remote_code=True,
        dtype=torch.float16,
        device_map={"": "cuda:0"},
        low_cpu_mem_usage=True,
    )

    print(f"[3/4] Merging LoRA adapter: {adapter_path}", flush=True)
    merged_model = PeftModel.from_pretrained(base_model, adapter_path)
    merged_model = merged_model.merge_and_unload()
    merged_model.eval()

    print(f"[4/4] Saving standalone model: {output_dir}", flush=True)
    merged_model.save_pretrained(
        output_dir,
        safe_serialization=True,
        max_shard_size="2GB",
    )
    tokenizer.save_pretrained(output_dir)

    total_bytes = sum(
        path.stat().st_size for path in output_dir.rglob("*") if path.is_file()
    )
    print(
        f"[OK] Merged model saved ({total_bytes / 1024**3:.2f} GiB)",
        flush=True,
    )


if __name__ == "__main__":
    main()
