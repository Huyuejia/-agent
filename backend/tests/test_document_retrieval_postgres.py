"""PostgreSQL 文档双路检索（Lexical + PgVector）集成测试。

- 通过 TEST_DATABASE_URL 连接
- 验证 Alembic 0002 迁移、schema（vector/gin）、中文词法检索、
  cosine distance 排序、空结果与 error_codes NULL 唯一性
- 标记：postgres / integration
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = [pytest.mark.postgres, pytest.mark.integration]


def _alembic_upgrade() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="module")
def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL 未设置，跳过 PostgreSQL 集成测试")
    eng = create_engine(TEST_DATABASE_URL)
    with eng.connect():
        pass
    return eng


@pytest.fixture(scope="module")
def migrated(engine):
    _alembic_upgrade()
    return engine


@pytest.fixture()
def session(migrated):
    SessionLocal = sessionmaker(bind=migrated, autocommit=False, autoflush=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _insert_doc_with_chunks(session, chunks):
    """插入一个 documents 行与多个 chunks（不 commit，由 fixture 回滚清理）。"""
    from app.models.document import Document
    from app.repositories.document_chunk import SQLAlchemyDocumentChunkRepository
    from app.retrieval.tokenizer import JiebaTokenizer

    doc = Document(
        filename="itest-retrieval.pdf",
        file_type="pdf",
        file_size=100,
        chunk_count=len(chunks),
    )
    session.add(doc)
    session.flush()

    repo = SQLAlchemyDocumentChunkRepository(session, JiebaTokenizer())
    for chunk_index, (content, location, embedding) in enumerate(chunks):
        repo.add_chunk(
            document_id=doc.id,
            chunk_index=chunk_index,
            content=content,
            location=location,
            embedding=embedding,
            embedding_model="bge-m3" if embedding is not None else None,
            embedding_version="v1" if embedding is not None else None,
        )
    session.flush()
    return doc.id


def _vec(value: float, index: int) -> list[float]:
    v = [0.0] * 1024
    v[index] = value
    return v


# ---------------------------------------------------------------------------
# 迁移与 schema
# ---------------------------------------------------------------------------

def test_alembic_upgrade_reaches_current_head(migrated):
    with migrated.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == "0004"


def test_embedding_column_is_vector_1024(migrated):
    with migrated.connect() as conn:
        fmt = conn.execute(
            text(
                "SELECT format_type(atttypid, atttypmod) "
                "FROM pg_attribute "
                "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
            )
        ).scalar()
    assert fmt == "vector(1024)"


def test_gin_index_on_search_vector_exists(migrated):
    with migrated.connect() as conn:
        indexdef = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'document_chunks' "
                "AND indexname = 'ix_document_chunks_search_vector'"
            )
        ).scalar()
    assert indexdef is not None
    assert "USING gin" in indexdef


# ---------------------------------------------------------------------------
# 中文词法检索
# ---------------------------------------------------------------------------

def test_chinese_lexical_query_returns_expected_chunk(session):
    content = "智家智能摄像头提供一年保修服务，支持七日内退货"
    _insert_doc_with_chunks(
        session,
        [
            (content, "第1页", None),
            ("智家智能网关支持 Zigbee 和 WiFi 协议", "第2页", None),
        ],
    )

    from app.repositories.lexical import SQLAlchemyLexicalRepository
    from app.retrieval import JiebaTokenizer, LexicalRetriever, MissPolicy, RetrievalMode, RetrievalStep

    retriever = LexicalRetriever(SQLAlchemyLexicalRepository(session), JiebaTokenizer())
    step = RetrievalStep(
        step_id="lexical",
        mode=RetrievalMode.LEXICAL,
        query="摄像头 保修",
        top_k=5,
        required=True,
        miss_policy=MissPolicy.STOP,
    )
    evidence = retriever.retrieve("摄像头 保修", step)
    assert evidence, "词法查询应命中 chunk"
    assert evidence[0].kind is RetrievalMode.LEXICAL
    assert "摄像头" in evidence[0].text
    assert evidence[0].raw_score is not None
    assert evidence[0].metadata["score_semantics"] == "ts_rank_cd"


def test_lexical_irrelevant_query_returns_empty(session):
    _insert_doc_with_chunks(
        session,
        [("智家智能摄像头提供一年保修服务", "第1页", None)],
    )

    from app.repositories.lexical import SQLAlchemyLexicalRepository
    from app.retrieval import JiebaTokenizer, LexicalRetriever, MissPolicy, RetrievalMode, RetrievalStep

    retriever = LexicalRetriever(SQLAlchemyLexicalRepository(session), JiebaTokenizer())
    step = RetrievalStep(
        step_id="lexical",
        mode=RetrievalMode.LEXICAL,
        query="火星探测",
        top_k=5,
        required=True,
        miss_policy=MissPolicy.STOP,
    )
    assert retriever.retrieve("火星探测计划", step) == []


def test_lexical_or_recall_with_colloquial_query(session):
    _insert_doc_with_chunks(
        session,
        [("智家智能摄像头提供一年保修服务，支持七日内退货", "第1页", None)],
    )

    from app.repositories.lexical import SQLAlchemyLexicalRepository
    from app.retrieval import JiebaTokenizer, LexicalRetriever, MissPolicy, RetrievalMode, RetrievalStep

    retriever = LexicalRetriever(SQLAlchemyLexicalRepository(session), JiebaTokenizer())
    query = "我想了解一下摄像头的保修政策"
    step = RetrievalStep(
        step_id="lexical",
        mode=RetrievalMode.LEXICAL,
        query=query,
        top_k=5,
        required=True,
        miss_policy=MissPolicy.STOP,
    )
    evidence = retriever.retrieve(query, step)
    assert evidence, "口语化查询应命中包含核心关键词（摄像头/保修）的 chunk"
    assert "摄像头" in evidence[0].text or "保修" in evidence[0].text


# ---------------------------------------------------------------------------
# 向量检索（cosine distance 精确排序）
# ---------------------------------------------------------------------------

def test_vector_search_sorts_by_cosine_distance(session):
    query_vec = _vec(1.0, 0)
    _insert_doc_with_chunks(
        session,
        [
            ("最相似：与查询同向", "a", query_vec),        # distance 0
            ("正交向量", "b", _vec(1.0, 1)),              # distance 1
            ("相反向量", "c", _vec(-1.0, 0)),             # distance 2
        ],
    )

    from app.repositories.vector import SQLAlchemyVectorRepository

    repo = SQLAlchemyVectorRepository(session)
    matches = repo.search_by_vector(
        query_vec, top_k=3, embedding_model="bge-m3", embedding_version="v1"
    )
    assert [m.content for m in matches] == ["最相似：与查询同向", "正交向量", "相反向量"]
    assert matches[0].distance == pytest.approx(0.0, abs=1e-6)
    assert matches[1].distance == pytest.approx(1.0, abs=1e-6)
    assert matches[2].distance == pytest.approx(2.0, abs=1e-6)


def test_vector_search_skips_null_embeddings(session):
    _insert_doc_with_chunks(
        session,
        [("未嵌入的 chunk", "第1页", None)],
    )

    from app.repositories.vector import SQLAlchemyVectorRepository

    repo = SQLAlchemyVectorRepository(session)
    assert (
        repo.search_by_vector(
            _vec(1.0, 0), top_k=5, embedding_model="bge-m3", embedding_version="v1"
        )
        == []
    )


def test_vector_search_isolates_by_model_and_version(session):
    from app.models.document import Document
    from app.repositories.document_chunk import SQLAlchemyDocumentChunkRepository
    from app.repositories.vector import SQLAlchemyVectorRepository
    from app.retrieval.tokenizer import JiebaTokenizer

    doc = Document(
        filename="itest-retrieval.pdf",
        file_type="pdf",
        file_size=100,
        chunk_count=3,
    )
    session.add(doc)
    session.flush()

    repo = SQLAlchemyDocumentChunkRepository(session, JiebaTokenizer())
    # 距离 0（最相似）但模型不符 → 必须被排除
    repo.add_chunk(
        document_id=doc.id,
        chunk_index=0,
        content="错误模型",
        location="b",
        embedding=_vec(1.0, 0),
        embedding_model="other-model",
        embedding_version="v1",
    )
    # 距离 0（最相似）但版本不符 → 必须被排除
    repo.add_chunk(
        document_id=doc.id,
        chunk_index=1,
        content="错误版本",
        location="c",
        embedding=_vec(1.0, 0),
        embedding_model="bge-m3",
        embedding_version="v2",
    )
    # 距离 1，但模型+版本均匹配 → 唯一应返回的 chunk
    repo.add_chunk(
        document_id=doc.id,
        chunk_index=2,
        content="正确模型正确版本",
        location="a",
        embedding=_vec(1.0, 1),
        embedding_model="bge-m3",
        embedding_version="v1",
    )
    session.flush()

    vrepo = SQLAlchemyVectorRepository(session)
    matches = vrepo.search_by_vector(
        _vec(1.0, 0), top_k=5, embedding_model="bge-m3", embedding_version="v1"
    )
    assert [m.content for m in matches] == ["正确模型正确版本"]
    assert matches[0].distance == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# error_codes NULL 唯一性
# ---------------------------------------------------------------------------

def test_error_code_null_uniqueness_enforced(session):
    from sqlalchemy.exc import IntegrityError

    from app.models.error_code import ErrorCode

    code = "E-DUP-NULL-TEST"
    session.add(ErrorCode(product_id=None, code=code, normalized_code=code))
    session.flush()
    session.add(ErrorCode(product_id=None, code=code, normalized_code=code))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
