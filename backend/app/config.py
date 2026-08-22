from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    database_url: str | None = None

    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "demo_user"
    mysql_password: str = "demo_pass"
    mysql_database: str = "customer_workbench"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "demo123456"

    chroma_persist_dir: str = "./backend/data/chroma"

    demo_user_id: int = 1
    demo_offline_mode: bool = False

    # Optional external intent-model service; rules remain the safe default.
    intent_model_url: str | None = None
    intent_model_timeout_seconds: float = 10.0

    # BGE-M3 local model
    bge_model_path: str = "./models/bge_m3"
    bge_model_name: str = "BAAI/bge-m3"
    bge_local_files_only: bool = True

    @field_validator("chroma_persist_dir", "bge_model_path")
    @classmethod
    def resolve_project_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute():
            return str(path)
        return str((PROJECT_ROOT / path).resolve())

    # Resolve the environment file from the repository root instead of the
    # process working directory. This keeps configuration consistent whether
    # uvicorn starts from the repository root or from backend/.
    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
