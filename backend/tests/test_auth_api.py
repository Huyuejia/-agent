"""Authentication, ownership, and document authorization API tests."""

import io
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.dependencies.retrieval import (
    get_document_indexing_service,
    get_document_search_service,
)
from app.models.base import Base
from app.models.conversation import Conversation, Message
from app.models.document import Document
from app.models.user import User
from app.postgres_database import get_postgres_db
from app.security.jwt import create_access_token
from app.security.passwords import hash_password


class FakeOrchestrator:
    def __init__(self):
        self.calls = []

    def route(self, message, conversation_id, db):
        self.calls.append((message, conversation_id))
        return {
            "conversation_id": conversation_id,
            "answer": "测试回答",
            "intent": "test",
            "confidence": 1.0,
            "source_type": "fallback",
            "sources": [],
            "handoff_required": False,
        }


class FakeSearchService:
    def search(self, query, top_k=4):
        return {
            "query": query,
            "answer": "测试检索",
            "source_type": "document_rag",
            "sources": [],
            "chunk_count": 0,
        }


class FakeIndexingService:
    def __init__(self):
        self.calls = []

    def index_document(self, document, chunks):
        self.calls.append((document, list(chunks)))


@pytest.fixture
def api_client():
    from app.main import create_app
    import app.api.conversations as conversations_api

    app = create_app(initialize_database=False)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Conversation.__table__,
            Message.__table__,
            Document.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    fake_orchestrator = FakeOrchestrator()
    fake_search = FakeSearchService()
    fake_indexing = FakeIndexingService()
    conversations_api._orchestrator = fake_orchestrator
    app.dependency_overrides[get_postgres_db] = override_db
    app.dependency_overrides[get_document_search_service] = lambda: fake_search
    app.dependency_overrides[get_document_indexing_service] = lambda: fake_indexing

    with TestClient(app) as client:
        yield client, SessionLocal, fake_orchestrator, fake_indexing

    conversations_api._orchestrator = None
    app.dependency_overrides.clear()
    engine.dispose()


def _register(client, email):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": "strong-password"},
    )


def _login(client, email):
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "strong-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_login_and_me(api_client):
    client, SessionLocal, _, _ = api_client
    response = _register(client, "Alice@Example.com")
    assert response.status_code == 201
    assert response.json()["email"] == "alice@example.com"
    assert response.json()["role"] == "user"

    db = SessionLocal()
    user = db.scalar(select(User).where(User.normalized_email == "alice@example.com"))
    assert user.password_hash != "strong-password"
    db.close()

    duplicate = _register(client, "alice@example.com")
    assert duplicate.status_code == 409
    token = _login(client, "ALICE@example.com")
    me = client.get("/api/auth/me", headers=_headers(token))
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"
    assert me.json()["role"] == "user"


@pytest.mark.parametrize("field", ["role", "is_admin"])
def test_registration_rejects_role_injection(api_client, field):
    client, _, _, _ = api_client
    payload = {
        "email": "role@example.com",
        "password": "strong-password",
        field: "admin" if field == "role" else True,
    }
    assert client.post("/api/auth/register", json=payload).status_code == 422


def test_login_uses_same_error_for_unknown_and_wrong_password(api_client):
    client, _, _, _ = api_client
    _register(client, "known@example.com")
    wrong = client.post(
        "/api/auth/login",
        json={"email": "known@example.com", "password": "wrong"},
    )
    unknown = client.post(
        "/api/auth/login",
        json={"email": "unknown@example.com", "password": "wrong"},
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_missing_token_protects_all_private_endpoints(api_client):
    client, _, _, _ = api_client
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/conversations").status_code == 401
    assert client.post(
        "/api/chat", json={"conversation_id": 1, "message": "hello"}
    ).status_code == 401
    assert client.post(
        "/api/documents/search", json={"query": "保修"}
    ).status_code == 401
    upload = client.post(
        "/api/documents",
        files={"file": ("test.txt", io.BytesIO(b"text"), "text/plain")},
    )
    assert upload.status_code == 401


def _signed_claims(**overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    claims.update(overrides)
    return claims


def _rsa_token(private_key, claims):
    value = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return jwt.encode(claims, value, algorithm="RS256")


def test_invalid_tokens_return_401_with_bearer_challenge(api_client, rsa_key_paths):
    client, _, _, _ = api_client
    valid_private = serialization.load_pem_private_key(
        rsa_key_paths[0].read_bytes(), password=None
    )
    other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tokens = [
        "malformed",
        _rsa_token(
            valid_private,
            _signed_claims(exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
        ),
        _rsa_token(valid_private, _signed_claims(iss="wrong")),
        _rsa_token(valid_private, _signed_claims(aud="wrong")),
        _rsa_token(other_private, _signed_claims()),
    ]
    for token in tokens:
        response = client.get("/api/auth/me", headers=_headers(token))
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_conversation_owner_can_chat_but_other_user_gets_404(api_client):
    client, SessionLocal, orchestrator, _ = api_client
    _register(client, "a@example.com")
    _register(client, "b@example.com")
    token_a = _login(client, "a@example.com")
    token_b = _login(client, "b@example.com")

    created = client.post(
        "/api/conversations?title=A的会话", headers=_headers(token_a)
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]
    own_chat = client.post(
        "/api/chat",
        json={"conversation_id": conversation_id, "message": "hello"},
        headers=_headers(token_a),
    )
    assert own_chat.status_code == 200
    calls_after_owner = len(orchestrator.calls)

    other_chat = client.post(
        "/api/chat",
        json={"conversation_id": conversation_id, "message": "steal"},
        headers=_headers(token_b),
    )
    assert other_chat.status_code == 404
    assert len(orchestrator.calls) == calls_after_owner
    db = SessionLocal()
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    assert conversation.user_id > 0
    assert db.scalars(
        select(Message).where(Message.conversation_id == conversation_id)
    ).all() == []
    db.close()


def _docx_bytes():
    from docx import Document as DocxDocument

    document = DocxDocument()
    document.add_paragraph("管理员上传权限测试文档")
    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    return output


def test_document_search_is_authenticated_and_upload_is_admin_only(api_client):
    client, SessionLocal, _, indexing = api_client
    _register(client, "reader@example.com")
    user_token = _login(client, "reader@example.com")
    search = client.post(
        "/api/documents/search",
        json={"query": "保修"},
        headers=_headers(user_token),
    )
    assert search.status_code == 200
    forbidden = client.post(
        "/api/documents",
        files={
            "file": (
                "test.docx",
                _docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=_headers(user_token),
    )
    assert forbidden.status_code == 403

    db = SessionLocal()
    admin = User(
        email="admin@example.com",
        normalized_email="admin@example.com",
        password_hash=hash_password("admin-password"),
        role="admin",
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    admin_token = create_access_token(admin.id)
    db.close()
    uploaded = client.post(
        "/api/documents",
        files={
            "file": (
                "test.docx",
                _docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=_headers(admin_token),
    )
    assert uploaded.status_code == 201, uploaded.text
    assert len(indexing.calls) == 1


def test_inactive_user_token_is_rejected(api_client):
    client, SessionLocal, _, _ = api_client
    _register(client, "disabled@example.com")
    token = _login(client, "disabled@example.com")
    db = SessionLocal()
    user = db.scalar(
        select(User).where(User.normalized_email == "disabled@example.com")
    )
    user.is_active = False
    db.commit()
    db.close()
    assert client.get("/api/auth/me", headers=_headers(token)).status_code == 401
