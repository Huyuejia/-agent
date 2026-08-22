# Customer Intelligence Workbench

智能家居客服工作台 — 基于 QLoRA 意图分类、Neo4j 知识图谱与文档 RAG 的演示系统。

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React + Vite + TypeScript |
| API | FastAPI + Pydantic + SQLAlchemy |
| 微调 | Qwen2.5-1.5B-Instruct + QLoRA (PEFT + bitsandbytes) |
| 图谱 | Neo4j 5 |
| 文档 RAG | Chroma + BAAI/bge-m3（设计默认 bge-small-zh-v1.5；本机通过 BGE_MODEL_PATH 使用本地 BGE-M3，模型文件不入仓库） |
| 关系库 | MySQL 8 |

## 快速启动

### 1. 环境准备

```bash
cp .env.example .env
```

后端始终读取项目根目录的 `.env`，因此从项目根目录或 `backend/`
启动时使用同一套配置。`.env` 只保存在本机，不提交到 Git。

### 2. 启动数据库

```bash
docker-compose up -d
```

等待健康检查通过：

```bash
docker-compose ps
```

### 3. 启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

## 项目结构

```
customer-intelligence-workbench/
├── backend/          FastAPI 应用
│   ├── app/
│   │   ├── api/      API 路由
│   │   ├── models/   SQLAlchemy 模型
│   │   ├── services/ 业务服务（分类器、图谱、RAG、编排）
│   │   └── schemas/  Pydantic 请求/响应模型
│   ├── data/         持久化数据（Chroma）
│   └── tests/        后端测试
├── frontend/         React 工作台
│   └── src/
├── training/         QLoRA 训练与评测脚本
│   ├── data/         训练/验证/测试 JSONL
│   └── reports/      评测报告
├── graph/            Neo4j 种子数据
├── scripts/          演示与种子脚本
├── docker-compose.yml
├── .env.example
└── README.md
```

## API 概览

| 接口 | 职责 |
|------|------|
| `POST /api/documents` | 上传 PDF/DOCX |
| `POST /api/conversations` | 创建会话 |
| `GET /api/conversations` | 会话列表 |
| `GET /api/conversations/{id}/messages` | 消息历史 |
| `POST /api/conversations/{id}/chat` | 意图识别 + 路由 + 回答 |
| `GET /api/evaluation` | 评测指标与错误样例 |

## 演示场景

1. 上传保修政策 PDF，追问退货规则 → 文档 RAG 带页码引用
2. 询问 Cam-A1 与 Hub-Z1 兼容性 → 知识图谱关系卡片
3. 投诉/模糊问题 → 人工跟进提示 (handoff_required=true)

## 局限

- 不包含用户认证、Redis 缓存、联网搜索、图片识别
- 知识图谱使用虚构品牌"智家"数据
- 模型评测指标需在云 GPU 训练完成后填入
