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

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "customer_workbench"
    postgres_url: str | None = None

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "demo123456"

    chroma_persist_dir: str = "./backend/data/chroma"

    demo_user_id: int = 1
    demo_offline_mode: bool = False

    # Optional external intent-model service; rules remain the safe default.
    intent_model_url: str | None = None
    intent_model_timeout_seconds: float = 10.0

    # Optional OpenAI-compatible LLM; retrieval works without it.
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "qwen-plus"
    llm_timeout_seconds: float = 30.0
    llm_temperature: float = 0.2
    llm_max_tokens: int = 512

    # BGE-M3 local model
    bge_model_path: str = "./models/bge_m3"
    bge_model_name: str = "BAAI/bge-m3"
    bge_model_version: str = "local-v1"
    bge_embedding_dimension: int = 1024
    bge_device: str = "auto"
    bge_batch_size: int = 4
    bge_local_files_only: bool = True

    @field_validator("chroma_persist_dir", "bge_model_path")
    @classmethod
    def resolve_project_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute():
            return str(path)
        return str((PROJECT_ROOT / path).resolve())

    @property
    def postgres_database_url(self) -> str:
        """PostgreSQL DSN（psycopg3），优先取显式 URL，否则由分项拼装。"""
        if self.postgres_url:
            return self.postgres_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # Resolve the environment file from the repository root instead of the
    # process working directory. This keeps configuration consistent whether
    # uvicorn starts from the repository root or from backend/.
    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
