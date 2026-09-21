"""Strict RS256 access-token signing and verification."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from jwt import InvalidTokenError

from app.config import Settings, settings


class AuthConfigurationError(RuntimeError):
    """Authentication key material is unavailable or invalid."""


class TokenValidationError(ValueError):
    """The presented access token is invalid."""


def _read_key(path_value: str, kind: str) -> str:
    path = Path(path_value)
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuthConfigurationError(f"JWT {kind} key is unavailable: {path}") from exc
    if not value.strip():
        raise AuthConfigurationError(f"JWT {kind} key is empty: {path}")
    return value


def create_access_token(
    user_id: int,
    *,
    configured: Settings = settings,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(
        seconds=configured.jwt_access_token_expire_seconds
    )
    claims = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": expires_at,
        "iss": configured.jwt_issuer,
        "aud": configured.jwt_audience,
    }
    private_key = _read_key(configured.jwt_private_key_path, "private")
    try:
        return jwt.encode(claims, private_key, algorithm="RS256")
    except (InvalidTokenError, ValueError, TypeError) as exc:
        raise AuthConfigurationError("JWT private key is invalid") from exc


def decode_access_token(
    token: str,
    *,
    configured: Settings = settings,
) -> dict[str, Any]:
    public_key = _read_key(configured.jwt_public_key_path, "public")
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=configured.jwt_issuer,
            audience=configured.jwt_audience,
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject.isdigit() or int(subject) <= 0:
            raise TokenValidationError("invalid token subject")
        return claims
    except TokenValidationError:
        raise
    except (InvalidTokenError, ValueError, TypeError, KeyError) as exc:
        raise TokenValidationError("invalid access token") from exc
