from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import create_tables


def create_app(initialize_database: bool = True) -> FastAPI:
    """构建 FastAPI 应用。

    initialize_database=False 时，lifespan 不调用 create_tables()，
    供测试使用内存 SQLite / Fake 服务，避免连接真实 PostgreSQL。
    """

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # 启动时建表（仅在生产初始化场景）
        if initialize_database:
            create_tables()
        yield

    application = FastAPI(
        title="Customer Intelligence Workbench",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由注册
    from app.api.documents import router as documents_router
    from app.api.conversations import router as conversations_router
    from app.api.auth import router as auth_router

    application.include_router(auth_router)
    application.include_router(documents_router)
    application.include_router(conversations_router)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    return application


# 模块级生产应用：默认初始化数据库
app = create_app()
