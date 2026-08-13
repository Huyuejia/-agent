from pydantic_settings import BaseSettings


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

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
