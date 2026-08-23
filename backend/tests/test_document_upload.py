"""
文档上传与 RAG 集成测试。

- 使用 fake deterministic embedding（1024 维），不加载 BGE-M3，不联网
- 覆盖：扩展名校验、大小限制、PDF/DOCX 解析、切块、检索、sources 结构
"""

import io
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 确保 backend 在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["BGE_LOCAL_FILES_ONLY"] = "false"  # 测试不加载真实模型


# ---------------------------------------------------------------------------
# Fake embedding — 固定 1024 维
# ---------------------------------------------------------------------------
EMBED_DIM = 1024


def _fake_embed_fn(texts: list[str]) -> list[list[float]]:
    """确定性 fake embedding：取文本 hash 的绝对值做向量。"""
    import hashlib

    vectors = []
    for t in texts:
        h = hashlib.sha256(t.encode()).digest()
        # 将 32 字节扩展为 1024 维
        vec = []
        for i in range(EMBED_DIM):
            b = h[i % 32]
            vec.append((b / 255.0) * 2 - 1)
        vectors.append(vec)
    return vectors


# ---------------------------------------------------------------------------
# 生成测试用 PDF / DOCX
# ---------------------------------------------------------------------------
def _make_test_pdf(text: str) -> io.BytesIO:
    """用 PyMuPDF 生成最小 PDF。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    # 将文本分行写入，避免超出页宽
    lines = text.split("\n")
    y = 50
    for line in lines:
        page.insert_text((50, y), line, fontsize=11)
        y += 16
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    buf.seek(0)
    return buf


def _make_test_docx(text: str) -> io.BytesIO:
    """用 python-docx 生成最小 DOCX。"""
    from docx import Document

    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# TestClient + fake RagService
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client() -> TestClient:
    """创建 TestClient，注入 fake embedding 的 RagService。"""
    from app.main import create_app
    from app.services.rag_service import RagService

    app = create_app(initialize_database=False)

    # 用临时目录做 Chroma 持久化
    tmpdir = tempfile.mkdtemp(prefix="chroma_test_")
    fake_rag = RagService(embed_fn=_fake_embed_fn, embed_dim=EMBED_DIM, persist_dir=tmpdir)

    # 注入 fake rag_service（惰性 getter 会在首次调用时发现已设置）
    import app.api.documents as doc_mod

    doc_mod._rag_service = fake_rag

    # 建表（使用测试数据库或跳过）
    # 这里只测业务逻辑链，不连真实 MySQL — 用内存 SQLite 替代
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import get_db
    from app.models.document import Base, Document, DocumentIndex

    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # 在内存 SQLite 里只建本测试用到的两张表（测试应用 initialize_database=False，
    # lifespan 不建表）。用 tables= 限制，避免共享 Base.metadata 里其它
    # PostgreSQL 专有表（如 document_chunks）污染 SQLite 建表。
    Base.metadata.create_all(
        bind=test_engine,
        tables=[Document.__table__, DocumentIndex.__table__],
    )
    TestingSessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()
    # 恢复 rag_service 为 None，避免测试副作用残留
    doc_mod._rag_service = None


# ===================================================================
# 失败测试
# ===================================================================

class TestValidationFailures:
    """先写失败测试：非法输入必须被拒绝。"""

    def test_reject_txt_file(self, client):
        """.txt 文件应返回 400。"""
        buf = io.BytesIO(b"hello world")
        resp = client.post(
            "/api/documents",
            files={"file": ("test.txt", buf, "text/plain")},
        )
        assert resp.status_code == 400
        assert "不支持" in resp.json()["detail"]

    def test_reject_no_extension(self, client):
        """无扩展名文件应返回 400。"""
        buf = io.BytesIO(b"hello world")
        resp = client.post(
            "/api/documents",
            files={"file": ("noext", buf, "application/octet-stream")},
        )
        assert resp.status_code == 400

    def test_reject_empty_filename(self, client):
        """空文件名应返回 400。"""
        buf = io.BytesIO(b"hello")
        resp = client.post(
            "/api/documents",
            files={"file": (None, buf)},
        )
        assert resp.status_code in (400, 422)

    def test_reject_oversize_file(self, client):
        """超过 10MB 的文件应返回 413。"""
        big_data = b"x" * (10 * 1024 * 1024 + 1)
        buf = io.BytesIO(big_data)
        resp = client.post(
            "/api/documents",
            files={"file": ("large.pdf", buf, "application/pdf")},
        )
        assert resp.status_code == 413
        assert "超过" in resp.json()["detail"]

    def test_search_empty_query(self, client):
        """空查询应返回 422（Pydantic 校验）。"""
        resp = client.post("/api/documents/search", json={"query": ""})
        assert resp.status_code == 422


# ===================================================================
# 通过测试
# ===================================================================

class TestPdfUploadAndSearch:
    """PDF 上传 → 切块 → 检索全链路。"""

    # 超 500 字的测试文本，确保产生多个 chunk
    TEST_TEXT = (
        "智家智能家居产品保修政策\n\n"
        "第一条 保修范围\n"
        "本保修政策适用于智家品牌旗下所有智能家居产品，包括但不限于智能摄像头、"
        "智能网关、传感器、门锁、照明及插座类产品。用户自购买之日起享受对应保修服务。\n\n"
        "第二条 保修期限\n"
        "不同产品适用不同的保修期限。标准产品享有一年标准保修，旗舰网关产品享有三年尊享保修，"
        "部分门锁产品享有两年延保服务。具体以产品包装内保修卡为准。\n\n"
        "第三条 退货政策\n"
        "用户可在购买后七日内无理由退货，前提是商品完好、配件齐全且不影响二次销售。"
        "超过七日但仍在保修期内的，若出现非人为损坏的质量问题，用户可选择换货或免费维修。"
        "人为损坏、自行拆修、不可抗力导致的故障不在免费保修范围内。\n\n"
        "第四条 维修流程\n"
        "用户可通过智家官方客服热线或微信小程序提交维修申请。客服将在两个工作日内响应，"
        "确认故障后安排上门取件或用户寄修。维修周期一般为五个工作日。\n\n"
        "第五条 免责条款\n"
        "因地震、火灾、水灾等不可抗力导致的产品损坏不在保修范围内。"
        "使用非智家官方配件导致的产品故障不在保修范围内。"
        "产品序列号被撕毁、涂改或无法辨认的，智家有权拒绝提供保修服务。\n\n"
        + "补充说明文本。 " * 50  # 扩充至足够切出多个 chunk
    )

    @pytest.fixture(scope="class")
    @classmethod
    def upload_result(cls, client):
        """上传一份 PDF 并返回响应。"""
        pdf_buf = _make_test_pdf(cls.TEST_TEXT)
        resp = client.post(
            "/api/documents",
            files={"file": ("智家保修政策.pdf", pdf_buf, "application/pdf")},
        )
        return resp

    def test_upload_pdf_success(self, upload_result):
        """PDF 上传应返回 201 且包含 chunk_count。"""
        assert upload_result.status_code == 201, upload_result.text
        data = upload_result.json()
        assert data["filename"] == "智家保修政策.pdf"
        assert data["file_type"] == "pdf"
        assert data["chunk_count"] > 0
        assert data["document_id"] > 0

    def test_chunks_have_multiple(self, upload_result):
        """长文本应产生多个 chunk。"""
        assert upload_result.json()["chunk_count"] >= 2

    def test_search_returns_sources(self, upload_result, client):
        """检索应返回 sources 数组且包含文件名和位置。"""
        resp = client.post("/api/documents/search", json={"query": "退货政策"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source_type"] == "document_rag"
        assert len(data["sources"]) >= 1
        first = data["sources"][0]
        assert "document_name" in first
        assert "location" in first
        assert "snippet" in first
        assert len(first["snippet"]) > 0

    def test_search_answer_not_empty(self, client):
        """检索 answer 字段不应为空。"""
        resp = client.post("/api/documents/search", json={"query": "保修期限"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["answer"]) > 0

    def test_search_irrelevant_query(self, client):
        """对无关查询：fake embedding 下仍可能返回 chunks，仅验证响应结构稳定。"""
        resp = client.post("/api/documents/search", json={"query": "火星探测计划"})
        assert resp.status_code == 200
        data = resp.json()
        assert "query" in data
        assert "answer" in data
        assert data["source_type"] == "document_rag"
        assert isinstance(data["sources"], list)
        assert isinstance(data["chunk_count"], int)


class TestDocxUpload:
    """DOCX 上传基本验证。"""

    TEST_TEXT = (
        "智家智能门锁安装指南\n\n"
        "第一步：开箱检查\n"
        "请检查包装内包含门锁主体、锁芯、钥匙、电池及安装螺丝包。\n\n"
        "第二步：安装锁体\n"
        "将锁体放入门孔，使用螺丝固定。确保锁舌伸缩顺畅。\n\n"
        "第三步：安装面板\n"
        "连接前后面板排线，扣合面板并使用螺丝紧固。\n\n"
        + "补充说明文本。 " * 40
    )

    def test_upload_docx_success(self, client):
        """DOCX 上传应返回 201。"""
        buf = _make_test_docx(self.TEST_TEXT)
        resp = client.post(
            "/api/documents",
            files={"file": ("安装指南.docx", buf, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["file_type"] == "docx"
        assert data["chunk_count"] > 0


class TestChunkMetadata:
    """验证 chunk 的 metadata 结构。"""

    def test_chunk_metadata_fields(self, client):
        """上传后检索：返回的 sources 必须包含 document_name 和 location。"""
        # 用短文本确保 query 与 chunk 完全一致（fake embedding 无语义）
        short_text = "XYZ元数据校验测试文档内容"
        doc_name = "test_meta_xyz.pdf"
        buf = _make_test_pdf(short_text)
        resp = client.post(
            "/api/documents",
            files={"file": (doc_name, buf, "application/pdf")},
        )
        assert resp.status_code == 201, resp.text

        # 用与 chunk 完全一致的文本搜索
        sr = client.post("/api/documents/search", json={"query": short_text})
        assert sr.status_code == 200
        sources = sr.json()["sources"]
        assert len(sources) >= 1, f"query 与 chunk 一致，应命中: {sr.json()}"
        for s in sources:
            assert "document_name" in s, f"source 缺 document_name: {s}"
            assert "location" in s, f"source 缺 location: {s}"
            assert len(s["location"]) > 0
