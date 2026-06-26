from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import create_tables


@asynccontextmanager
async def lifespan(application: FastAPI):
    # 启动时建表
    create_tables()
    yield


app = FastAPI(
    title="Customer Intelligence Workbench",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册
from app.api.documents import router as documents_router  # noqa: E402

app.include_router(documents_router)


@app.get("/health")
def health():
    return {"status": "ok"}
