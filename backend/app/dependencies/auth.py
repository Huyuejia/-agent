"""FastAPI authentication and authorization dependencies."""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.models.user import User
from app.postgres_database import get_postgres_db
from app.security.jwt import AuthConfigurationError, TokenValidationError, decode_access_token


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效或已过期的访问令牌",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_postgres_db),
) -> User:
    try:
        claims = decode_access_token(token)
        user_id = int(claims["sub"])
    except (TokenValidationError, AuthConfigurationError, ValueError, KeyError):
        raise _credentials_error()

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _credentials_error()
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user
