"""Password hashing and strict RS256 JWT tests."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.security.jwt import (
    AuthConfigurationError,
    TokenValidationError,
    create_access_token,
    decode_access_token,
)
from app.security.passwords import hash_password, verify_password


def _settings(rsa_key_paths, **overrides) -> Settings:
    values = {
        "jwt_private_key_path": str(rsa_key_paths[0]),
        "jwt_public_key_path": str(rsa_key_paths[1]),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _encode(claims, private_path, algorithm="RS256"):
    key = private_path.read_text(encoding="utf-8")
    return jwt.encode(claims, key, algorithm=algorithm)


def _claims(configured, **overrides):
    now = datetime.now(timezone.utc)
    values = {
        "sub": "1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": configured.jwt_issuer,
        "aud": configured.jwt_audience,
    }
    values.update(overrides)
    return values


def test_argon2id_hash_and_verification():
    password_hash = hash_password("correct horse battery staple")
    assert password_hash != "correct horse battery staple"
    assert password_hash.startswith("$argon2id$")
    assert verify_password("correct horse battery staple", password_hash)
    assert not verify_password("wrong password", password_hash)
    assert not verify_password("anything", "damaged-hash")


def test_access_token_round_trip(rsa_key_paths):
    configured = _settings(rsa_key_paths)
    token = create_access_token(42, configured=configured)
    claims = decode_access_token(token, configured=configured)
    assert claims["sub"] == "42"
    assert claims["iss"] == configured.jwt_issuer
    assert claims["aud"] == configured.jwt_audience


@pytest.mark.parametrize(
    "mutator",
    [
        lambda config, claims: claims.pop("sub"),
        lambda config, claims: claims.update(exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
        lambda config, claims: claims.update(iss="wrong-issuer"),
        lambda config, claims: claims.update(aud="wrong-audience"),
    ],
    ids=["missing-sub", "expired", "wrong-issuer", "wrong-audience"],
)
def test_rejects_invalid_required_claims(rsa_key_paths, mutator):
    configured = _settings(rsa_key_paths)
    claims = _claims(configured)
    mutator(configured, claims)
    token = _encode(claims, rsa_key_paths[0])
    with pytest.raises(TokenValidationError):
        decode_access_token(token, configured=configured)


def test_rejects_wrong_signature(rsa_key_paths, tmp_path):
    configured = _settings(rsa_key_paths)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_path = tmp_path / "other-private.pem"
    other_path.write_bytes(
        other_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    token = _encode(_claims(configured), other_path)
    with pytest.raises(TokenValidationError):
        decode_access_token(token, configured=configured)


def test_rejects_non_rs256_algorithm(rsa_key_paths):
    configured = _settings(rsa_key_paths)
    token = jwt.encode(_claims(configured), "shared-secret-that-is-long-enough", algorithm="HS256")
    with pytest.raises(TokenValidationError):
        decode_access_token(token, configured=configured)


def test_missing_key_files_fail_explicitly(tmp_path):
    configured = Settings(
        _env_file=None,
        jwt_private_key_path=str(tmp_path / "missing-private.pem"),
        jwt_public_key_path=str(tmp_path / "missing-public.pem"),
    )
    with pytest.raises(AuthConfigurationError):
        create_access_token(1, configured=configured)
    with pytest.raises(AuthConfigurationError):
        decode_access_token("not-a-token", configured=configured)
