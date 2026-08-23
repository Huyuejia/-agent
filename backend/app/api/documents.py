"""
文档上传与检索 API
POST /api/documents        — 上传 PDF/DOCX
POST /api/documents/search — 检索
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.retrieval import get_document_indexing_service
from app.indexing import IndexableChunk, IndexableDocument
from app.indexing.service import DocumentIndexingService
from app.models.document import Document, DocumentIndex
from app.schemas.document import SearchRequest, SearchResponse, SourceItem, UploadResponse
from app.services.document_service import (
    chunk_text_docx,
    chunk_text_pdf,
    extract_text,
    validate_file,
)
from app.services.rag_service import RagService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

# 惰性 RagService — 测试可在首次调用前注入 fake
_rag_service: RagService | None = None


def _get_rag_service() -> RagService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RagService()
    return _rag_service


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    indexing_service: DocumentIndexingService = Depends(get_document_indexing_service),
):
    """上传 PDF 或 DOCX 文档，解析、切块、向量化并入库。"""
    # 1. 校验（返回原始文件字节数）
    file_size = validate_file(file)

    # 2. 提取文本
    raw_text, file_type = extract_text(file)
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="文档中未提取到可读文本")

    # 3. 切块
    document_name = file.filename or "unknown"
    if file_type == "pdf":
        chunks = chunk_text_pdf(raw_text, document_name)
    else:
        chunks = chunk_text_docx(raw_text, document_name)

    if not chunks:
        raise HTTPException(status_code=400, detail="文档切块后无有效内容")

    # 4. 写入 MySQL documents 表
    doc_record = Document(
        filename=document_name,
        file_type=file_type,
        file_size=file_size,
        chunk_count=len(chunks),
    )
    db.add(doc_record)
    db.flush()  # 获取 document_id
    retrieval_document = IndexableDocument(
        document_id=doc_record.id,
        filename=document_name,
        file_type=file_type,
        file_size=file_size,
    )
    retrieval_chunks = [
        IndexableChunk(
            chunk_index=index,
            content=chunk.text,
            location=chunk.location,
            metadata={"document_name": chunk.document_name},
        )
        for index, chunk in enumerate(chunks)
    ]

    # 5. 向量化 + 写入 Chroma
    texts = [c.text for c in chunks]
    metadatas = [
        {
            "document_id": doc_record.id,
            "document_name": c.document_name,
            "location": c.location,
            "snippet": c.text,
        }
        for c in chunks
    ]
    chroma_ids = _get_rag_service().add_chunks(texts, metadatas)

    # 6. 写入 MySQL document_indexes 表
    for i, chunk in enumerate(chunks):
        idx_record = DocumentIndex(
            document_id=doc_record.id,
            document_name=chunk.document_name,
            chunk_index=i,
            location=chunk.location,
            snippet=chunk.text,
            chroma_id=chroma_ids[i] if i < len(chroma_ids) else "",
        )
        db.add(idx_record)

    db.commit()
    # PostgreSQL 检索索引是可重建的派生数据。失败不回滚历史上传链路，
    # 后续可通过补录命令幂等重试。
    try:
        indexing_service.index_document(retrieval_document, retrieval_chunks)
    except Exception:
        logger.exception("PostgreSQL 文档检索索引写入失败，等待后续补录")

    return UploadResponse(
        document_id=doc_record.id,
        filename=document_name,
        file_type=file_type,
        file_size=file_size,
        chunk_count=len(chunks),
        message=f"文档 {document_name} 上传成功，共 {len(chunks)} 个切块",
    )


@router.post("/search", response_model=SearchResponse)
async def search_documents(payload: SearchRequest):
    """在已上传文档中检索相关片段，返回 top-4 结果与来源引用。"""
    result = _get_rag_service().search(payload.query, top_k=4)

    sources = [SourceItem(**s) for s in result["sources"]]

    return SearchResponse(
        query=result["query"],
        answer=result["answer"],
        source_type=result["source_type"],
        sources=sources,
        chunk_count=result["chunk_count"],
    )
