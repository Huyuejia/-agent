# Phase 1：Redis 精确检索缓存

## 开工基线

- 分支：`feat/hybrid-retrieval`
- 基线提交：`4db77e0`
- 开工前必须确认 `git status --short` 只有本任务单为未提交文件；若存在其他代码改动，立即停止并报告。
- 不执行 `git commit`、`git push`、数据库删除或 Docker volume 删除。

## 本阶段目标

为货号、订单号、序列号和错误码的精确检索增加 Redis cache-aside 缓存。PostgreSQL 仍是唯一事实来源；Redis 不可用时必须降级到 PostgreSQL，不能导致聊天失败，也不能改为相似度检索。

本阶段不实现 Go、不缓存向量或图检索、不把会话放入 Redis、不实现异步任务。

## 固定设计

1. 新建实现 `ExactRepository` 协议的缓存装饰器，不能把 Redis 逻辑写进 `ExactRetriever` 或 SQLAlchemy 模型。
2. 缓存粒度为单个 `(entity_type, normalized_value)`，key 格式：
   `ciw:exact:v1:{entity_type}:{normalized_value}`。
3. 正命中默认 TTL 为 300 秒；未命中使用明确的负缓存标记，默认 TTL 为 30 秒。
4. 缓存内容使用带版本、字段明确的 JSON，能完整还原 `ResolvedEntity`；不得使用 pickle。
5. Redis 读取、反序列化或写入失败时记录 warning，并直接调用底层 PostgreSQL repository。该策略为 fail-open。
6. 批量输入和重复标识符必须保持现有精确检索语义；不得因缓存改变 EXACT 未命中时的 STOP 策略。
7. Redis 客户端必须惰性创建，导入模块或应用启动时不能因为 Redis 未启动而崩溃。

## CC 实现范围

- 在 `docker-compose.yml` 增加 Redis 7 服务、端口和 healthcheck。缓存是派生数据，本阶段不增加持久化 volume。
- 在 `.env.example` 和 `backend/app/config.py` 增加 Redis URL、正缓存 TTL、负缓存 TTL 配置及边界校验。
- 在 `backend/requirements.txt` 固定一个实际测试通过的 `redis-py` 版本。
- 新增 Redis 缓存 repository，并在生产 `RetrievalChatService` 组装处包装 `SQLAlchemyExactRepository`。
- 使用 fake Redis/fake repository 编写单元测试，至少覆盖：
  - 首次 miss 查询 PostgreSQL 并写正缓存；
  - 正缓存 hit 不查询 PostgreSQL；
  - 负缓存 hit 不查询 PostgreSQL；
  - TTL 参数正确；
  - Redis get 失败时 PostgreSQL 降级；
  - Redis set 失败不影响本次 PostgreSQL 结果；
  - JSON 损坏时 PostgreSQL 降级；
  - 混合实体、重复值和结果顺序。
- 增加一个真实 Redis 集成测试；通过 `TEST_REDIS_URL` 显式启用，未设置时必须明确 skip，不能伪装成 passed。
- 保持现有 PostgreSQL、Neo4j、pgvector、GPU 和 API 契约不变。

## 验收命令

CC 应自行执行并在报告中记录真实输出：

```bash
docker compose config
docker compose up -d redis
docker compose ps redis
backend/.venv/bin/python -m pip check
backend/.venv/bin/python -m pytest -s backend/tests/<新增单元测试文件>
TEST_REDIS_URL=redis://localhost:6379/0 backend/.venv/bin/python -m pytest -s backend/tests/<新增集成测试文件>
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/customer_workbench TEST_REDIS_URL=redis://localhost:6379/0 backend/.venv/bin/python -m pytest -s backend/tests
git diff --check
```

不得执行 `docker compose down -v`。

## 完成后停止并写报告

将报告写入 `docs/development/reports/phase-1-redis-exact-cache-report.md`，内容只包括：

- 改动文件清单；
- 缓存命中、负缓存和 fail-open 的实现位置；
- 各项测试的真实 passed/skipped 数；
- Redis 容器状态；
- 未解决问题或与任务单的偏差；
- `git status --short` 和 `git diff --stat`。

写完报告后停止，不 commit，不自行开始 Go Gateway。
