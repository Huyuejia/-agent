# Customer Intelligence Workbench

智能家居客服工作台 — 基于 QLoRA 意图分类、Neo4j 知识图谱与文档 RAG 的演示系统。

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React + Vite + TypeScript |
| API 网关 | Go 1.27 标准库反向代理 + RS256 JWT 预校验 |
| API | FastAPI + Pydantic + SQLAlchemy |
| 微调 | Qwen2.5-1.5B-Instruct + QLoRA (PEFT + bitsandbytes) |
| 图谱 | Neo4j 5 |
| 缓存 | Redis 7（精确检索 cache-aside） |
| 文档 RAG | PostgreSQL FTS + pgvector + RRF + 本地 BGE-M3（GPU） |
| 关系库 | PostgreSQL 16（业务数据、会话、文档与向量统一存储） |

## 快速启动

### 1. 环境准备

```bash
cp .env.example .env
```

后端始终读取项目根目录的 `.env`，因此从项目根目录或 `backend/`
启动时使用同一套配置。`.env` 只保存在本机，不提交到 Git。

### 2. 启动数据库

```bash
docker compose up -d postgres neo4j redis
```

等待健康检查通过：

```bash
docker compose ps
```

### 3. 启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head
python -m app.seed
python -m app.seed_demo_knowledge
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. 启动 API 网关

保持 FastAPI 运行，再从项目根目录执行：

```bash
docker compose up -d gateway
curl http://localhost:8080/ready
```

### 5. 启动前端

```bash
cd frontend
npm install
npm run dev
```

## 项目结构

```
customer-intelligence-workbench/
├── gateway/          Go API 网关、JWT 预校验与反向代理
├── backend/          FastAPI 应用
│   ├── app/
│   │   ├── api/      API 路由
│   │   ├── models/   SQLAlchemy 模型
│   │   ├── services/ 业务服务（分类器、图谱、混合检索、编排）
│   │   └── schemas/  Pydantic 请求/响应模型
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
| `POST /api/auth/register` | 注册普通用户 |
| `POST /api/auth/login` | 获取短期 RS256 Access Token |
| `GET /api/auth/me` | 获取当前用户 |
| `POST /api/documents` | 管理员上传 PDF/DOCX |
| `POST /api/documents/search` | 登录用户检索文档 |
| `POST /api/conversations` | 创建当前用户会话 |
| `POST /api/chat` | 校验会话归属后执行意图识别、检索与回答 |

## 检索质量基线

从项目根目录运行确定性 routing golden dataset：

```bash
backend/.venv/bin/python scripts/evaluate_retrieval_routing.py
```

当前 ranking 指标实现位于 `backend/app/evaluation/metrics.py`；扩充多文档相关性标注后再报告 Recall@k/MRR。

## Workflow/Agent Eval Harness

版本控制案例位于 `evaluation/agent_v2_eval_cases.jsonl`。无模型凭据的确定性 lane 仅验证 Harness、fixture、grader 和 JSONL 输出：

```bash
cd backend
.venv/bin/python -m app.evaluation.run_harness \
  --cases ../evaluation/agent_v2_eval_cases.jsonl \
  --executor-factory app.evaluation.fixture_runner:build_deterministic_runner \
  --output /tmp/agent-v2-eval-results.jsonl
```

真实 Workflow/Agent/auto 比较通过同一个命令接入显式的 `module_path:callable_name` executor factory。该 factory 必须为三种模式提供相同 fixture、用户输入、Tool Adapter 和故障注入；只有该运行 lane 需要本地服务和模型凭据。凭据不写入 EvalCase，也不需要外部 Eval 平台或 UI。

确定性单元测试不调用模型或外部服务：

```bash
cd backend
.venv/bin/python -m pytest tests/test_eval_harness.py -q
```

## CI 与 PR

仓库通过 GitHub Actions 在每个 Pull Request 上并行执行：

- 后端 Python 单元测试（不下载 GPU 模型依赖）
- Go 网关测试
- 前端 ESLint 与生产构建

涉及真实 PostgreSQL、Redis 或 Neo4j 的集成测试保留为本地显式运行，基础 CI 聚焦快速、可重复的回归检查。

## 演示场景

1. 上传保修政策 PDF，追问退货规则 → 文档 RAG 带页码引用
2. 询问 Cam-A1 与 Hub-Z1 兼容性 → 知识图谱关系卡片
3. 投诉/模糊问题 → 人工跟进提示 (handoff_required=true)

## 局限

- 当前只有 Access Token，没有 Refresh Token、撤销列表或密钥轮换
- 网关采用简单固定窗口限流，尚未实现可信代理链配置、TLS 终止与服务发现
- 不包含联网搜索和图片识别
- 知识图谱使用虚构品牌"智家"数据
- 模型评测指标需在云 GPU 训练完成后填入
