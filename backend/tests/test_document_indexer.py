"""DocumentIndexer 纯单元测试。"""

import pytest

from app.indexing import DocumentIndexer, IndexableChunk, IndexableDocument


class FakeEmbedder:
    model_name = "BAAI/bge-m3"
    model_version = "test-v1"
    dimension = 1024

    def __init__(self, output=None):
        self.output = output
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.output is not None:
            return self.output
        return [[float(index)] * self.dimension for index, _ in enumerate(texts)]


class FakeWriter:
    def __init__(self):
        self.calls = []

    def replace_document(self, document, chunks, **kwargs):
        self.calls.append((document, list(chunks), kwargs))


def _document():
    return IndexableDocument(
        document_id=7,
        filename="保修政策.pdf",
        file_type="pdf",
        file_size=1234,
    )


def _chunks():
    return [
        IndexableChunk(0, "摄像头保修一年", "第1页", {"section": "保修"}),
        IndexableChunk(1, "七日内可以退货", "第2页"),
    ]


def test_batches_embeddings_and_writes_model_identity():
    embedder = FakeEmbedder()
    writer = FakeWriter()

    result = DocumentIndexer(embedder, writer).index_document(_document(), _chunks())

    assert embedder.calls == [["摄像头保修一年", "七日内可以退货"]]
    document, chunks, kwargs = writer.calls[0]
    assert document.document_id == 7
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert chunks[0].metadata == {"section": "保修"}
    assert kwargs == {
        "embedding_model": "BAAI/bge-m3",
        "embedding_version": "test-v1",
    }
    assert result.chunk_count == 2


def test_empty_document_removes_existing_index_without_loading_vectors():
    embedder = FakeEmbedder()
    writer = FakeWriter()

    result = DocumentIndexer(embedder, writer).index_document(_document(), [])

    assert embedder.calls == [[]]
    assert writer.calls[0][1] == []
    assert result.chunk_count == 0


def test_rejects_duplicate_chunk_indices_before_embedding():
    embedder = FakeEmbedder()
    writer = FakeWriter()
    chunks = [
        IndexableChunk(0, "a", "p1"),
        IndexableChunk(0, "b", "p2"),
    ]

    with pytest.raises(ValueError, match="不得重复"):
        DocumentIndexer(embedder, writer).index_document(_document(), chunks)
    assert embedder.calls == []
    assert writer.calls == []


def test_rejects_embedding_count_mismatch():
    embedder = FakeEmbedder(output=[[0.0] * 1024])

    with pytest.raises(ValueError, match="数量"):
        DocumentIndexer(embedder, FakeWriter()).index_document(_document(), _chunks())


def test_rejects_wrong_embedder_dimension():
    embedder = FakeEmbedder()
    embedder.dimension = 512

    with pytest.raises(ValueError, match="pgvector"):
        DocumentIndexer(embedder, FakeWriter())
