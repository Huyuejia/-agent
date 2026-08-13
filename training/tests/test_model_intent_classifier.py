"""Tests for the reusable model-backed intent classifier."""

from training.intent_classifier import ModelIntentClassifier


class FakeRuntime:
    """Small stand-in for the real Transformers model runtime."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def generate(self, text: str) -> str:
        self.messages.append(text)
        if "退货" in text:
            return '{"intent":"return_refund","confidence":0.93}'
        return '{"intent":"complaint","confidence":0.88}'


def test_model_runtime_is_loaded_once_and_reused():
    runtime = FakeRuntime()
    loader_calls: list[tuple[str, str]] = []

    def load_runtime(base_model: str, adapter_path: str) -> FakeRuntime:
        loader_calls.append((base_model, adapter_path))
        return runtime

    classifier = ModelIntentClassifier(
        base_model="Qwen/Qwen2.5-1.5B-Instruct",
        adapter_path="local-adapter",
        runtime_factory=load_runtime,
    )

    first = classifier.predict("我要退货")
    second = classifier.predict("我要投诉")

    assert loader_calls == [
        ("Qwen/Qwen2.5-1.5B-Instruct", "local-adapter")
    ]
    assert runtime.messages == ["我要退货", "我要投诉"]
