"""
验证 application factory 的数据库初始化开关。

- initialize_database=False 时，lifespan 不得调用 create_tables()
- initialize_database=True（默认）时，lifespan 调用 create_tables()
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.main as main_mod


def test_create_app_disabled_does_not_create_tables(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(main_mod, "create_tables", lambda: calls.append(True))

    app = main_mod.create_app(initialize_database=False)
    with TestClient(app) as client:
        client.get("/health")

    assert calls == [], "initialize_database=False 时不应调用 create_tables()"


def test_create_app_default_creates_tables(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(main_mod, "create_tables", lambda: calls.append(True))

    app = main_mod.create_app()
    with TestClient(app) as client:
        client.get("/health")

    assert calls == [True], "默认参数应调用 create_tables()"


def test_module_level_app_is_production() -> None:
    """模块级 app 仍存在且为生产配置。"""
    assert main_mod.app is not None
    assert main_mod.app.title == "Customer Intelligence Workbench"
