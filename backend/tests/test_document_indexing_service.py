"""DocumentIndexingService 的提交/回滚边界。"""

import pytest

from app.indexing import IndexableDocument, IndexingResult
from app.indexing.service import TransactionalDocumentIndexingService


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeIndexer:
    def __init__(self, error=None):
        self.error = error

    def index_document(self, document, chunks):
        if self.error:
            raise self.error
        return IndexingResult(document.document_id, len(chunks), "bge-m3", "v1")


def _document():
    return IndexableDocument(1, "doc.pdf", "pdf", 100)


def test_commits_successful_indexing():
    session = FakeSession()
    service = TransactionalDocumentIndexingService(session, FakeIndexer())

    result = service.index_document(_document(), [])

    assert result.document_id == 1
    assert session.commits == 1
    assert session.rollbacks == 0


def test_rolls_back_and_preserves_original_error():
    session = FakeSession()
    service = TransactionalDocumentIndexingService(
        session, FakeIndexer(error=RuntimeError("embedding failed"))
    )

    with pytest.raises(RuntimeError, match="embedding failed"):
        service.index_document(_document(), [])
    assert session.commits == 0
    assert session.rollbacks == 1
