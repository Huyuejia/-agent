# Phase 1 Redis 精确检索缓存 — 完成报告

## 改动文件清单

新增：

- `backend/app/repositories/exact_cache.py` — `RedisExactRepositoryCache` 装饰器 + 编码/解码 + 惰性 Redis 客户端工厂
- `backend/tests/test_exact_cache.py` — 单元测试（fake Redis / fake repository）
- `backend/tests/test_exact_cache_redis.py` — 真实 Redis 集成测试（`TEST_REDIS_URL` 显式启用）

修改：

- `backend/app/config.py` — 新增 `redis_url`、`redis_exact_cache_ttl_seconds`、`redis_exact_cache_negative_ttl_seconds`、`redis_connect_timeout_seconds`、`redis_socket_timeout_seconds` 及 TTL/超时边界校验
- `backend/app/services/retrieval_chat.py` — `RetrievalChatService` 增加 `exact_repository_factory`；生产组装处包装 `SQLAlchemyExactRepository`
- `.env.example` — 新增 `REDIS_URL` / `REDIS_PORT` / `REDIS_EXACT_CACHE_TTL_SECONDS` / `REDIS_EXACT_CACHE_NEGATIVE_TTL_SECONDS` / `REDIS_CONNECT_TIMEOUT_SECONDS` / `REDIS_SOCKET_TIMEOUT_SECONDS`
- `docker-compose.yml` — 新增 Redis 7 服务（`redis:7-alpine`）、端口映射与 healthcheck（无持久化 volume）
- `backend/requirements.txt` — 固定 `redis==5.3.1`
- `backend/tests/test_config.py` — 新增 Redis 超时配置默认值与边界校验回归测试

## 二道门四项修复

1. **负缓存命中不刷新 TTL**：`resolve()` 用 `negative_hits` 集合记录负缓存命中的键，重建结果时对其 `continue`，不再调用 `_set` 重写负缓存标记（修复原实现命中负缓存后仍重写、导致 TTL 被反复刷新的问题）。
2. **正缓存实体校验 entity_type/normalized_value 与请求 key 一致**：`resolve()` 在正命中后校验 `entity.entity_type == 请求 type` 且 `entity.normalized_value == 请求 value`；不一致则记 warning 并回源 PostgreSQL（重写正确缓存），防止脏键/串键返回错误结果。
3. **TEST_REDIS_URL 已设置但连接失败时 fail**：集成测试 fixture 仅在 `TEST_REDIS_URL` 未设置时 `skip`；已设置时不再捕获连接异常为 skip，并新增 `test_redis_connection` 对 `ping()` 断言，连接失败直接失败（实测 3 failed，非 skip）。
4. **Redis 客户端可配置连接/读取超时**：`create_redis_client()` 接受 `connect_timeout_seconds` / `socket_timeout_seconds`（默认读取 `Settings`），透传到 `redis.Redis.from_url(..., socket_connect_timeout=…, socket_timeout=…)`；配置新增边界校验（必须 > 0）。

## 缓存命中、负缓存与 fail-open 实现位置

均在 `backend/app/repositories/exact_cache.py`：

- 缓存键：`redis_key()` → `ciw:exact:v1:{entity_type}:{normalized_value}`
- 正缓存命中：`encode_entity()` / `decode()` 返回 `("positive", entity)`，`resolve()` 校验 key 一致后直接复用、不回源 PostgreSQL
- 负缓存标记：`encode_miss()`（显式 `{"v":1,"kind":"miss"}`）/ `decode()` 返回 `("negative", None)`；命中后跳过回源且不重写（不刷新 TTL）
- TTL：`resolve()` 中正缓存写 `self._positive_ttl`（默认 300s）、负缓存写 `self._negative_ttl`（默认 30s）
- fail-open（任一失败记 warning 并回源 PostgreSQL）：
  - 客户端创建失败：`_client()`（惰性工厂，失败返回 `None` → 直接回源）
  - Redis 读取失败：`_get()`
  - 反序列化/JSON 损坏/字段缺失/版本不符：`decode()` 返回 `("miss", None)`
  - 写入失败：`_set()`（不影响本次 PostgreSQL 结果）
- 惰性客户端：`create_redis_client()` 首次调用才 `import redis` 并 `Redis.from_url`（进程内共享，带连接/读取超时）；导入模块 / 应用启动不要求 Redis 在线

## 各项测试真实结果

| 命令 | 结果 |
| --- | --- |
| `backend/.venv/bin/python -m pytest -s backend/tests/test_exact_cache.py` | **16 passed** |
| `backend/.venv/bin/python -m pytest -s backend/tests/test_config.py` | **4 passed** |
| `TEST_REDIS_URL=redis://localhost:6379/0 backend/.venv/bin/python -m pytest -s backend/tests/test_exact_cache_redis.py` | **3 passed** |
| `TEST_REDIS_URL` 未设置时运行集成测试 | **3 skipped**（明确 skip，非伪装 passed） |
| `TEST_REDIS_URL=redis://localhost:6399/0`（端口不可达）运行集成测试 | **3 failed**（连接失败 fail，非 skip） |
| `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/customer_workbench TEST_REDIS_URL=redis://localhost:6379/0 backend/.venv/bin/python -m pytest -s backend/tests` | **214 passed**（0 skipped） |
| `backend/.venv/bin/python -m pip check` | No broken requirements found |
| `docker compose config` | 通过（无报错） |
| `git diff --check` | 通过（clean） |

## Redis 容器状态

```
NAME        IMAGE            COMMAND                  SERVICE   CREATED         STATUS                    PORTS
ciw-redis   redis:7-alpine   "docker-entrypoint.s…"   redis     18 minutes ago   Up 18 minutes (healthy)   0.0.0.0:6379->6379/tcp
```

## 未解决问题或与任务单的偏差

- 无未解决问题。
- 设计说明（非偏差）：批量结果按 `sku → order → serial → error_code` 的规范类型块顺序返回，类型内按去重后的首次出现顺序；这与 `SQLAlchemyExactRepository` 的类型块顺序一致。EXACT 未命中的 STOP 策略不受缓存影响——缓存装饰器对未命中仍返回空列表，STOP 由 executor/retriever 层按 `MissPolicy` 处理，未做任何相似度降级。
- 缓存为派生数据，本阶段未增加 Redis 持久化 volume（符合任务单要求）。

## git status --short 与 git diff --stat

`git status --short`：

```
 M .env.example
 M backend/app/config.py
 M backend/app/services/retrieval_chat.py
 M backend/requirements.txt
 M backend/tests/test_config.py
 M docker-compose.yml
?? backend/app/repositories/exact_cache.py
?? backend/tests/test_exact_cache.py
?? backend/tests/test_exact_cache_redis.py
?? docs/development/
```

`git diff --stat`：

```
 .env.example                           |  8 ++++++++
 backend/app/config.py                  | 27 +++++++++++++++++++++++++++
 backend/app/services/retrieval_chat.py | 22 +++++++++++++++++++++-
 backend/requirements.txt               |  1 +
 backend/tests/test_config.py           | 14 ++++++++++++++
 docker-compose.yml                     | 13 +++++++++++++
 6 files changed, 84 insertions(+), 1 deletion(-)
```
