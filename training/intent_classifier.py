"""Reusable LoRA-backed intent classifier for evaluation and serving."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Protocol


INTENT_LABELS = {
    "product_consultation",
    "compatibility",
    "order_status",
    "shipping",
    "return_refund",
    "warranty_fault",
    "complaint",
    "human_handoff",
}

SYSTEM_PROMPT = (
    "你是一个智能家居客服意图分类器。根据用户输入，判断意图并返回 JSON。\n"
    "意图类别: product_consultation, compatibility, order_status, shipping, "
    "return_refund, warranty_fault, complaint, human_handoff\n"
    "只返回 JSON，不要添加任何解释。\n"
    '示例输出: {"intent":"compatibility","confidence":0.92}'
)


class IntentRuntime(Protocol):
    """Minimal interface implemented by real and fake model runtimes."""

    def generate(self, text: str) -> str:
        """Return the model's raw text response for one user message."""


def parse_prediction(raw_output: str) -> tuple[str, float]:
    """Parse and validate a model-generated intent JSON object."""
    candidates = [raw_output.strip()]
    json_match = re.search(r"\{[^}]+\}", raw_output)
    if json_match:
        candidates.append(json_match.group())

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            intent = str(payload.get("intent", "")).strip()
            confidence = float(payload.get("confidence", 0.0))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            continue

        if intent in INTENT_LABELS and 0.0 <= confidence <= 1.0:
            return intent, confidence

    raise ValueError(f"Invalid intent model output: {raw_output!r}")


class TransformersIntentRuntime:
    """Loads the base model and LoRA adapter once, then reuses them."""

    def __init__(self, base_model: str, adapter_path: str) -> None:
        # Heavy dependencies stay inside the real runtime so lightweight unit
        # tests can import this module without installing PyTorch/Transformers.
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(
            base_model,
            trust_remote_code=True,
            padding_side="left",
        )
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        base = AutoModelForCausalLM.from_pretrained(
            base_model,
            device_map="auto",
            trust_remote_code=True,
            dtype=torch.bfloat16,
        )
        self._model = PeftModel.from_pretrained(base, adapter_path)
        self._model = self._model.merge_and_unload()
        self._model.eval()

    def generate(self, text: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        prompt = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._tokenizer(prompt, return_tensors="pt").to(
            self._model.device
        )

        with self._torch.inference_mode():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=64,
                do_sample=False,
                pad_token_id=(
                    self._tokenizer.pad_token_id
                    or self._tokenizer.eos_token_id
                ),
            )

        generated_tokens = outputs[0][inputs["input_ids"].shape[1] :]
        return self._tokenizer.decode(
            generated_tokens,
            skip_special_tokens=True,
        )


class ModelIntentClassifier:
    """Stable ``predict`` interface backed by one reusable model runtime."""

    def __init__(
        self,
        base_model: str,
        adapter_path: str,
        runtime_factory: Callable[[str, str], IntentRuntime] | None = None,
    ) -> None:
        factory = runtime_factory or TransformersIntentRuntime
        self._runtime = factory(base_model, adapter_path)

    def predict(self, text: str) -> tuple[str, float]:
        raw_output = self._runtime.generate(text)
        return parse_prediction(raw_output)

