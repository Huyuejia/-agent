"""Tests for the ONNX Runtime-backed intent classifier."""

from training.onnx_intent_classifier import OnnxIntentClassifier


class FakeOnnxRuntime:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def generate(self, text: str) -> str:
        self.messages.append(text)
        if "退货" in text:
            return '{"intent":"return_refund","confidence":0.95}'
        return '{"intent":"complaint","confidence":0.90}'


def test_onnx_runtime_is_loaded_once_and_reused():
    runtime = FakeOnnxRuntime()
    loader_calls: list[tuple[str, str]] = []

    def load_runtime(model_path: str, provider: str) -> FakeOnnxRuntime:
        loader_calls.append((model_path, provider))
        return runtime

    classifier = OnnxIntentClassifier(
        model_path="local-onnx-model",
        provider="CUDAExecutionProvider",
        runtime_factory=load_runtime,
    )

    first = classifier.predict("我要退货")
    second = classifier.predict("我要投诉")

    assert loader_calls == [
        ("local-onnx-model", "CUDAExecutionProvider")
    ]
    assert runtime.messages == ["我要退货", "我要投诉"]
    assert first == ("return_refund", 0.95)
    assert second == ("complaint", 0.90)
