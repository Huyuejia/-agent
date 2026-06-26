"""
Pydantic schemas: 上传响应 / 检索请求 / 检索响应
"""

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    document_id: int
    filename: str
    file_type: str
    file_size: int
    chunk_count: int
    message: str


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="检索查询文本")


class SourceItem(BaseModel):
    document_name: str
    location: str
    snippet: str


class SearchResponse(BaseModel):
    query: str
    answer: str
    source_type: str = "document_rag"
    sources: list[SourceItem]
    chunk_count: int  # 本次命中的总 chunk 数
