"""Registration, login, and current-user endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.postgres_database import get_postgres_db
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.security.jwt import AuthConfigurationError, create_access_token
from app.security.passwords import hash_password, verify_password


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _user_response(user: User) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, role=user.role)


@router.post("/register", response_model=UserResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_postgres_db)):
    normalized_email = _normalize_email(str(payload.email))
    user = User(
        email=normalized_email,
        normalized_email=normalized_email,
        password_hash=hash_password(payload.password),
        role="user",
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该邮箱已注册") from exc
    return _user_response(user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_postgres_db)):
    normalized_email = _normalize_email(str(payload.email))
    user = db.scalar(select(User).where(User.normalized_email == normalized_email))
    if user is None or not user.is_active or not verify_password(
        payload.password, user.password_hash
    ):
        raise HTTPException(
            status_code=401,
            detail="邮箱或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        token = create_access_token(user.id)
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail="认证服务配置不可用") from exc
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_access_token_expire_seconds,
    )


@router.get("/me", response_model=UserResponse)
def current_user(user: User = Depends(get_current_user)):
    return _user_response(user)
