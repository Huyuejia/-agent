"""
文档上传与检索 API
POST /api/documents        — 上传 PDF/DOCX 到 PostgreSQL + pgvector
POST /api/documents/search — PostgreSQL 全文 + 向量混合检索
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user, require_admin
from app.dependencies.retrieval import (
    get_document_indexing_service,
    get_document_search_service,
)
from app.indexing import IndexableChunk, IndexableDocument
from app.indexing.service import DocumentIndexingService
from app.models.document import Document
from app.models.user import User
from app.postgres_database import get_postgres_db
from app.schemas.document import SearchRequest, SearchResponse, SourceItem, UploadResponse
from app.services.document_search import PostgresDocumentSearchService
from app.services.document_service import (
    chunk_text_docx,
    chunk_text_pdf,
    extract_text,
    validate_file,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_postgres_db),
    indexing_service: DocumentIndexingService = Depends(get_document_indexing_service),
    _admin: User = Depends(require_admin),
):
    """上传 PDF 或 DOCX，原子写入 PostgreSQL 文档元数据和检索片段。"""
    file_size = validate_file(file)
    raw_text, file_type = extract_text(file)
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="文档中未提取到可读文本")

    document_name = file.filename or "unknown"
    if file_type == "pdf":
        chunks = chunk_text_pdf(raw_text, document_name)
    else:
        chunks = chunk_text_docx(raw_text, document_name)
    if not chunks:
        raise HTTPException(status_code=400, detail="文档切块后无有效内容")

    doc_record = Document(
        filename=document_name,
        file_type=file_type,
        file_size=file_size,
        chunk_count=len(chunks),
    )
    db.add(doc_record)
    db.flush()

    descriptor = IndexableDocument(
        document_id=doc_record.id,
        filename=document_name,
        file_type=file_type,
        file_size=file_size,
    )
    indexable_chunks = [
        IndexableChunk(
            chunk_index=index,
            content=chunk.text,
            location=chunk.location,
            metadata={"document_name": chunk.document_name},
        )
        for index, chunk in enumerate(chunks)
    ]
    try:
        indexing_service.index_document(descriptor, indexable_chunks)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="文档索引写入失败") from exc

    return UploadResponse(
        document_id=doc_record.id,
        filename=document_name,
        file_type=file_type,
        file_size=file_size,
        chunk_count=len(chunks),
        message=f"文档 {document_name} 上传成功，共 {len(chunks)} 个切块",
    )


@router.post("/search", response_model=SearchResponse)
async def search_documents(
    payload: SearchRequest,
    search_service: PostgresDocumentSearchService = Depends(
        get_document_search_service
    ),
    _current_user: User = Depends(get_current_user),
):
    """在 PostgreSQL 文档索引中执行词法 + 向量 RRF 检索。"""
    result = search_service.search(payload.query, top_k=4)
    sources = [SourceItem(**source) for source in result["sources"]]
    return SearchResponse(
        query=result["query"],
        answer=result["answer"],
        source_type=result["source_type"],
        sources=sources,
        chunk_count=result["chunk_count"],
    )
