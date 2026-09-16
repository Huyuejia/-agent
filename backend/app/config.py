from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "customer_workbench"
    postgres_url: str | None = None

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "demo123456"

    # Redis 精确检索缓存（cache-aside；Redis 不可用时 fail-open 回源 PostgreSQL）
    redis_url: str = "redis://localhost:6379/0"
    redis_exact_cache_ttl_seconds: int = 300
    redis_exact_cache_negative_ttl_seconds: int = 30
    redis_connect_timeout_seconds: float = 2.0
    redis_socket_timeout_seconds: float = 2.0

    demo_offline_mode: bool = False

    # Short-lived RS256 access tokens. Keys stay in local ignored files.
    jwt_private_key_path: str = "./secrets/jwt-private.pem"
    jwt_public_key_path: str = "./secrets/jwt-public.pem"
    jwt_algorithm: str = "RS256"
    jwt_issuer: str = "customer-intelligence-auth"
    jwt_audience: str = "customer-intelligence-api"
    jwt_access_token_expire_seconds: int = 900

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

    # Thin in-memory Pi runtime adapter (Node/TypeScript over JSONL stdio)
    pi_node_command: str = "node"
    pi_model_provider: str = "openai"
    pi_model: str = "gpt-5-mini"
    pi_runtime_timeout_seconds: float = 60.0
    agent_max_tool_calls: int = 6

    # BGE-M3 local model
    bge_model_path: str = "./models/bge_m3"
    bge_model_name: str = "BAAI/bge-m3"
    bge_model_version: str = "local-v1"
    bge_embedding_dimension: int = 1024
    bge_device: str = "auto"
    bge_batch_size: int = 4
    bge_local_files_only: bool = True

    @field_validator("bge_model_path")
    @classmethod
    def resolve_project_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute():
            return str(path)
        return str((PROJECT_ROOT / path).resolve())

    @field_validator("jwt_private_key_path", "jwt_public_key_path")
    @classmethod
    def resolve_jwt_key_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute():
            return str(path)
        return str((PROJECT_ROOT / path).resolve())

    @field_validator("jwt_algorithm")
    @classmethod
    def _rs256_only(cls, value: str) -> str:
        if value != "RS256":
            raise ValueError("JWT algorithm must be RS256")
        return value

    @field_validator("jwt_access_token_expire_seconds")
    @classmethod
    def _positive_access_token_ttl(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("JWT access token TTL must be positive")
        return value

    @field_validator(
        "redis_exact_cache_ttl_seconds",
        "redis_exact_cache_negative_ttl_seconds",
    )
    @classmethod
    def _positive_redis_ttl(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Redis 缓存 TTL 必须为正整数")
        return value

    @field_validator(
        "redis_connect_timeout_seconds",
        "redis_socket_timeout_seconds",
    )
    @classmethod
    def _positive_redis_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Redis 超时配置必须为正数")
        return value

    @field_validator("pi_runtime_timeout_seconds")
    @classmethod
    def _positive_pi_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Pi runtime timeout must be positive")
        return value

    @field_validator("agent_max_tool_calls")
    @classmethod
    def _positive_agent_tool_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Agent tool-call limit must be positive")
        return value

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
