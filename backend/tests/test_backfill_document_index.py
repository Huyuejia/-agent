"""历史文档索引补录测试。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.backfill_document_index import backfill_documents
from app.models.base import Base
from app.models.document import Document, DocumentIndex


class FakeIndexingService:
    def __init__(self, failing_ids=None):
        self.failing_ids = set(failing_ids or [])
        self.calls = []

    def index_document(self, document, chunks):
        self.calls.append((document, list(chunks)))
        if document.document_id in self.failing_ids:
            raise RuntimeError("fake failure")


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[Document.__table__, DocumentIndex.__table__],
    )
    return Session(engine)


def _seed(db):
    db.add_all(
        [
            Document(id=1, filename="a.pdf", file_type="pdf", file_size=10, chunk_count=1),
            Document(id=2, filename="b.docx", file_type="docx", file_size=20, chunk_count=1),
            DocumentIndex(
                document_id=1,
                document_name="a.pdf",
                chunk_index=0,
                location="第1页",
                snippet="摄像头保修",
                chroma_id="legacy-a",
            ),
            DocumentIndex(
                document_id=2,
                document_name="b.docx",
                chunk_index=0,
                location="第1段",
                snippet="网关协议",
                chroma_id="legacy-b",
            ),
        ]
    )
    db.commit()


def test_backfills_documents_in_stable_order_with_metadata():
    db = _session()
    _seed(db)
    service = FakeIndexingService()

    summary = backfill_documents(db, service)

    assert summary.indexed == 2
    assert summary.failed == 0
    assert [call[0].document_id for call in service.calls] == [1, 2]
    assert service.calls[0][1][0].metadata["legacy_chroma_id"] == "legacy-a"


def test_can_filter_ids_and_continues_after_one_failure():
    db = _session()
    _seed(db)
    service = FakeIndexingService(failing_ids={1})

    summary = backfill_documents(db, service, document_ids=[1, 2])

    assert summary.indexed == 1
    assert summary.failed == 1
    assert summary.failures == {1: "fake failure"}


def test_empty_id_filter_does_nothing():
    db = _session()
    _seed(db)
    service = FakeIndexingService()

    summary = backfill_documents(db, service, document_ids=[])

    assert summary.indexed == 0
    assert service.calls == []
