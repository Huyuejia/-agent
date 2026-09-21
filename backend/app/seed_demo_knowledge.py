"""幂等写入一份虚构的演示客服知识文档到 PostgreSQL/pgvector。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.indexing import IndexableChunk, IndexableDocument
from app.indexing.service import DocumentIndexingService
from app.models.document import Document
from app.services.document_service import chunk_text_docx

DEMO_FILENAME = "智家售后政策（演示）.docx"
DEMO_TEXT = """智家智能家居产品售后政策（虚构演示数据）

保修范围
本政策适用于智家品牌的摄像头、网关、传感器、门锁、照明和插座类产品。标准产品享有一年保修，Hub-Z1 网关享有三年保修，部分门锁享有两年保修。

退货政策
购买后七日内支持无理由退货。商品应保持完好、配件齐全，并且不影响二次销售。超过七日但仍在保修期内，非人为质量问题可申请换货或免费维修。

维修流程
用户可通过客服提交维修申请。客服在两个工作日内响应，确认故障后安排寄修；一般维修周期为五个工作日。

免责条款
人为损坏、自行拆修、不可抗力或使用非官方配件造成的故障不属于免费保修范围。产品序列号无法辨认时，需要人工核验购买凭证。
"""


def seed_demo_knowledge(
    session: Session,
    indexing_service: DocumentIndexingService,
) -> int:
    """创建或重建演示文档索引，返回 document_id。"""
    document = session.scalar(
        select(Document).where(Document.filename == DEMO_FILENAME).limit(1)
    )
    if document is None:
        document = Document(
            filename=DEMO_FILENAME,
            file_type="docx",
            file_size=len(DEMO_TEXT.encode("utf-8")),
            chunk_count=0,
        )
        session.add(document)
        session.flush()

    chunks = chunk_text_docx(DEMO_TEXT, DEMO_FILENAME)
    descriptor = IndexableDocument(
        document_id=document.id,
        filename=DEMO_FILENAME,
        file_type="docx",
        file_size=len(DEMO_TEXT.encode("utf-8")),
    )
    indexing_service.index_document(
        descriptor,
        [
            IndexableChunk(
                chunk_index=index,
                content=chunk.text,
                location=chunk.location,
                metadata={"document_name": DEMO_FILENAME, "demo": True},
            )
            for index, chunk in enumerate(chunks)
        ],
    )
    return document.id


def main() -> int:
    from app.dependencies.retrieval import get_bge_m3_embedder, get_retrieval_tokenizer
    from app.indexing.document_indexer import DocumentIndexer
    from app.indexing.service import TransactionalDocumentIndexingService
    from app.postgres_database import get_postgres_session_factory
    from app.repositories.document_index import SQLAlchemyDocumentIndexWriter

    session = get_postgres_session_factory()()
    try:
        writer = SQLAlchemyDocumentIndexWriter(session, get_retrieval_tokenizer())
        indexer = DocumentIndexer(get_bge_m3_embedder(), writer)
        service = TransactionalDocumentIndexingService(session, indexer)
        document_id = seed_demo_knowledge(session, service)
        print(f"demo_document_id={document_id}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
