from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
