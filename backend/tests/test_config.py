from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_ENV_FILE, PROJECT_ROOT, Settings


def test_default_env_file_is_repository_root() -> None:
    expected_root = Path(__file__).resolve().parents[2]

    assert PROJECT_ROOT == expected_root
    assert DEFAULT_ENV_FILE == expected_root / ".env"
    assert Path(Settings.model_config["env_file"]) == expected_root / ".env"


def test_default_env_file_is_independent_of_working_directory(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert Path(Settings.model_config["env_file"]).is_absolute()
    assert Path(Settings.model_config["env_file"]) == DEFAULT_ENV_FILE


def test_relative_data_paths_are_resolved_from_repository_root() -> None:
    configured = Settings(
        _env_file=None,
        bge_model_path="./models/bge_m3",
    )

    assert Path(configured.bge_model_path) == (
        PROJECT_ROOT / "models/bge_m3"
    )


def test_redis_timeout_defaults_and_boundary_validation() -> None:
    configured = Settings(_env_file=None)
    assert configured.redis_connect_timeout_seconds == 2.0
    assert configured.redis_socket_timeout_seconds == 2.0



def test_jwt_defaults_and_boundary_validation() -> None:
    configured = Settings(_env_file=None)
    assert configured.jwt_algorithm == "RS256"
    assert configured.jwt_issuer == "customer-intelligence-auth"
    assert configured.jwt_audience == "customer-intelligence-api"
    assert configured.jwt_access_token_expire_seconds == 900
    assert Path(configured.jwt_private_key_path).is_absolute()
    assert Path(configured.jwt_public_key_path).is_absolute()

    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_access_token_expire_seconds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_algorithm="HS256")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, redis_connect_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, redis_socket_timeout_seconds=-1)
