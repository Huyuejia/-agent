"""本地 BGE-M3 Embedder：懒加载、配置与输出校验。"""

import math

import pytest

from app.embeddings import BgeM3Embedder, Embedder, EmbeddingError


class FakeModel:
    def __init__(self, dimension=1024, output=None):
        self.dimension = dimension
        self.output = output
        self.calls = []

    def get_sentence_embedding_dimension(self):
        return self.dimension

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        if self.output is not None:
            return self.output
        return [[float(index)] * self.dimension for index, _ in enumerate(texts)]


class FakeLoader:
    def __init__(self, model):
        self.model = model
        self.calls = []

    def __call__(self, model_path, *, local_files_only, device):
        self.calls.append((model_path, local_files_only, device))
        return self.model


def _embedder(tmp_path, model=None, **kwargs):
    loader = FakeLoader(model or FakeModel())
    embedder = BgeM3Embedder(
        model_path=tmp_path,
        model_name="BAAI/bge-m3",
        model_version="local-v1",
        dimension=1024,
        device="cpu",
        batch_size=4,
        loader=loader,
        **kwargs,
    )
    return embedder, loader


def test_is_lazy_and_loads_model_only_once(tmp_path):
    embedder, loader = _embedder(tmp_path)

    assert loader.calls == []
    assert embedder.dimension == 1024
    first = embedder.embed(["摄像头", "保修"])
    second = embedder.embed(["网关"])

    assert len(loader.calls) == 1
    assert len(first) == 2
    assert len(second) == 1
    assert loader.model.calls[0][1]["normalize_embeddings"] is True
    assert loader.model.calls[0][1]["show_progress_bar"] is False
    assert loader.model.calls[0][1]["batch_size"] == 4


def test_empty_batch_does_not_load_model(tmp_path):
    embedder, loader = _embedder(tmp_path)

    assert embedder.embed([]) == []
    assert loader.calls == []


def test_metadata_and_protocol(tmp_path):
    embedder, _ = _embedder(tmp_path)

    assert isinstance(embedder, Embedder)
    assert embedder.model_name == "BAAI/bge-m3"
    assert embedder.model_version == "local-v1"


def test_rejects_missing_model_directory(tmp_path):
    missing = tmp_path / "missing"
    embedder = BgeM3Embedder(
        model_path=missing,
        model_name="BAAI/bge-m3",
        model_version="v1",
    )

    with pytest.raises(EmbeddingError, match="不存在"):
        embedder.embed(["query"])


def test_rejects_loaded_model_dimension_mismatch(tmp_path):
    embedder, _ = _embedder(tmp_path, model=FakeModel(dimension=512))

    with pytest.raises(EmbeddingError, match="模型维度"):
        embedder.embed(["query"])


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ([], "数量"),
        ([[0.0] * 512], "维度"),
        ([[math.nan] + [0.0] * 1023], "非有限"),
        ([["bad"] + [0.0] * 1023], "数值"),
    ],
)
def test_validates_embedding_output(tmp_path, output, message):
    embedder, _ = _embedder(tmp_path, model=FakeModel(output=output))

    with pytest.raises(EmbeddingError, match=message):
        embedder.embed(["query"])


def test_rejects_blank_text_and_invalid_configuration(tmp_path):
    embedder, _ = _embedder(tmp_path)
    with pytest.raises(ValueError, match="空文本"):
        embedder.embed(["  "])

    with pytest.raises(ValueError, match="model_version"):
        BgeM3Embedder(
            model_path=tmp_path,
            model_name="BAAI/bge-m3",
            model_version="",
        )
