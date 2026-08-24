from pathlib import Path

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
