"""
文档解析、校验与切块服务。
不依赖 Neo4j、不调用 LLM、不接触 embedding。
"""

import io
from dataclasses import dataclass
from typing import List

import fitz  # PyMuPDF
from docx import Document as DocxDocument
from fastapi import UploadFile

from app.config import settings

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
CHUNK_SIZE_CHARS = 500
CHUNK_OVERLAP_CHARS = 80


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Chunk:
    """切块后的文本片段及其元数据。"""

    text: str
    document_name: str
    location: str  # 如 "第 2 页" 或 "段落 5-7"


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_file(file: UploadFile) -> int:
    """上传文件校验：扩展名 + 大小。不合法时抛出 HTTPException。
    返回原始文件字节数，供调用方写入数据库时使用。"""
    from fastapi import HTTPException

    # 扩展名
    if file.filename is None:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    ext = file.filename.lower()
    if not any(ext.endswith(allowed) for allowed in ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 '{file.filename}'，仅接受 .pdf / .docx",
        )

    # 读取全部内容以校验大小
    content = file.file.read()
    size = len(content)
    if size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小 {size / 1024 / 1024:.1f} MB 超过 10 MB 限制",
        )
    # 回退指针，供后续解析使用
    file.file.seek(0)
    return size


# ---------------------------------------------------------------------------
# 文本提取
# ---------------------------------------------------------------------------
def extract_text(file: UploadFile) -> tuple[str, str]:
    """
    从 PDF 或 DOCX 提取纯文本。
    返回 (文本, 扩展名)，扩展名不含点号。
    """
    ext = file.filename.lower()
    content = file.file.read()
    file.file.seek(0)

    if ext.endswith(".pdf"):
        return _extract_pdf(content), "pdf"
    elif ext.endswith(".docx"):
        return _extract_docx(content), "docx"
    else:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")


def _extract_pdf(content: bytes) -> str:
    """PyMuPDF 逐页提取文本，每页末尾追加换行。"""
    doc = fitz.open(stream=content, filetype="pdf")
    parts: list[str] = []
    for page in doc:
        text = page.get_text()
        if text:
            parts.append(text)
    doc.close()
    return "\n".join(parts)


def _extract_docx(content: bytes) -> str:
    """python-docx 逐段提取文本。"""
    doc = DocxDocument(io.BytesIO(content))
    parts: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 切块 (500 字 / 80 字重叠)
# ---------------------------------------------------------------------------
def chunk_text_pdf(full_text: str, document_name: str) -> list[Chunk]:
    """
    PDF 文本按页切分后再做滑动窗口切块。
    策略：先按换页符 \x0c 或连续两个换行分段，
    若某段超过 CHUNK_SIZE_CHARS 则继续滑动窗口切分。
    """
    chunks: list[Chunk] = []
    # 尝试按换页符分段（PyMuPDF 每页间用 \n 连接，我们按双换行粗略分页）
    pages = _split_into_pages(full_text)

    for page_idx, page_text in enumerate(pages, start=1):
        if not page_text.strip():
            continue
        sub_chunks = _sliding_window(page_text, document_name, f"第 {page_idx} 页")
        chunks.extend(sub_chunks)

    return chunks


def chunk_text_docx(full_text: str, document_name: str) -> list[Chunk]:
    """
    DOCX 文本按段落切分：累计段落直到接近 500 字。
    """
    paragraphs = [p.strip() for p in full_text.split("\n") if p.strip()]
    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_len = 0
    para_start = 1
    para_end = 1

    for i, para in enumerate(paragraphs, start=1):
        buffer.append(para)
        buffer_len += len(para)
        para_end = i

        if buffer_len >= CHUNK_SIZE_CHARS:
            location = _para_location(para_start, para_end)
            chunks.append(
                Chunk(
                    text="\n".join(buffer),
                    document_name=document_name,
                    location=location,
                )
            )
            # 重叠：保留最后一条可能跨界的段落
            overlap_text = ""
            overlap_len = 0
            while buffer and overlap_len < CHUNK_OVERLAP_CHARS:
                last = buffer.pop()
                overlap_len += len(last)
                overlap_text = last + "\n" + overlap_text
            buffer = [overlap_text.strip()] if overlap_text.strip() else []
            buffer_len = overlap_len
            para_start = para_end

    # 收尾
    if buffer:
        location = _para_location(para_start, para_end)
        chunks.append(
            Chunk(text="\n".join(buffer), document_name=document_name, location=location)
        )

    return chunks


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------
def _split_into_pages(full_text: str) -> list[str]:
    """将全文按连续两个以上换行符粗分为'页'。"""
    import re

    pages = re.split(r"\n{2,}", full_text)
    # 合并过小的片段到前一段
    merged: list[str] = []
    for page in pages:
        page = page.strip()
        if not page:
            continue
        if merged and len(page) < 100:
            merged[-1] = merged[-1] + "\n" + page
        else:
            merged.append(page)
    return merged


def _sliding_window(text: str, document_name: str, location: str) -> list[Chunk]:
    """在单页/单段内做滑动窗口切块。"""
    chunks: list[Chunk] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + CHUNK_SIZE_CHARS, text_len)
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(
                Chunk(text=chunk_text, document_name=document_name, location=location)
            )
        if end >= text_len:
            break
        start = end - CHUNK_OVERLAP_CHARS
        if start <= 0:
            start = end  # 避免死循环

    return chunks


def _para_location(start: int, end: int) -> str:
    if start == end:
        return f"段落 {start}"
    return f"段落 {start}-{end}"
