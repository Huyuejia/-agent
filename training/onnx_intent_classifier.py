"""ONNX Runtime-backed intent classifier for deployment serving."""

from __future__ import annotations

from collections.abc import Callable

from training.intent_classifier import SYSTEM_PROMPT, IntentRuntime, parse_prediction


class OnnxIntentRuntime:
    """Loads one exported ONNX model and reuses its CUDA session."""

    def __init__(
        self,
        model_path: str,
        provider: str = "CUDAExecutionProvider",
    ) -> None:
        # Import PyTorch first so ONNX Runtime can reuse its CUDA/cuDNN shared
        # libraries in the WSL environment.
        import torch  # noqa: F401
        from optimum.onnxruntime import ORTModelForCausalLM
        from transformers import AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            local_files_only=True,
            trust_remote_code=True,
            padding_side="left",
            # Transformers 4.57.x can mis-detect local Qwen2 tokenizers as
            # Mistral-family tokenizers. Keep the Qwen2 rules unchanged.
            fix_mistral_regex=False,
        )
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        self._model = ORTModelForCausalLM.from_pretrained(
            model_path,
            provider=provider,
            use_io_binding=(provider == "CUDAExecutionProvider"),
        )

    @property
    def providers(self) -> list[str]:
        """Return the execution providers used by the ONNX session."""
        return list(self._model.providers)

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

        pad_token_id = self._tokenizer.pad_token_id
        if pad_token_id is None:
            pad_token_id = self._tokenizer.eos_token_id

        outputs = self._model.generate(
            **inputs,
            max_new_tokens=32,
            do_sample=False,
            pad_token_id=pad_token_id,
        )
        generated_tokens = outputs[0][inputs["input_ids"].shape[1] :]
        return self._tokenizer.decode(
            generated_tokens,
            skip_special_tokens=True,
        )


class OnnxIntentClassifier:
    """Stable ``predict`` interface backed by ONNX Runtime."""

    def __init__(
        self,
        model_path: str,
        provider: str = "CUDAExecutionProvider",
        runtime_factory: Callable[[str, str], IntentRuntime] | None = None,
    ) -> None:
        factory = runtime_factory or OnnxIntentRuntime
        self._runtime = factory(model_path, provider)

    def predict(self, text: str) -> tuple[str, float]:
        raw_output = self._runtime.generate(text)
        return parse_prediction(raw_output)
