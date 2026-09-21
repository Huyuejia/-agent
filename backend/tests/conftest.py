"""Shared test-only RSA keys; no key material is committed or reused."""

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def rsa_key_paths(tmp_path_factory) -> tuple[Path, Path]:
    key_dir = tmp_path_factory.mktemp("jwt-keys")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path = key_dir / "private.pem"
    public_path = key_dir / "public.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


@pytest.fixture(scope="session", autouse=True)
def configure_test_jwt_keys(rsa_key_paths):
    from app.config import settings

    old_private = settings.jwt_private_key_path
    old_public = settings.jwt_public_key_path
    settings.jwt_private_key_path = str(rsa_key_paths[0])
    settings.jwt_public_key_path = str(rsa_key_paths[1])
    try:
        yield
    finally:
        settings.jwt_private_key_path = old_private
        settings.jwt_public_key_path = old_public
