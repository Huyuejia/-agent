# Phase 2 JWT 身份认证与权限闭环 — 完成报告

## 改动文件与职责

认证核心：

- `backend/app/models/user.py`：User 模型、角色约束、active 状态。
- `backend/app/schemas/auth.py`：注册、登录、token 和当前用户响应 schema；注册拒绝额外角色字段。
- `backend/app/security/passwords.py`：Argon2id 哈希与安全校验。
- `backend/app/security/jwt.py`：RS256 Access JWT 签发、严格 claims 验证和密钥错误。
- `backend/app/dependencies/auth.py`：当前用户与管理员依赖。
- `backend/app/api/auth.py`：注册、登录、当前用户接口。
- `backend/app/create_admin.py`：使用 `getpass` 的交互式管理员创建/提升工具。

数据与 API：

- `backend/alembic/versions/0004_jwt_auth.py`：创建 users，将历史 `demo_user_id` 无损迁移为 `user_id` 外键。
- `backend/app/models/conversation.py`：会话改为真实 `user_id`。
- `backend/app/api/conversations.py`：创建会话自动绑定用户；聊天同时校验 ID 和归属。
- `backend/app/api/documents.py`：直接检索要求登录，上传仅管理员。
- `backend/app/migrate_legacy_sqlite.py`：旧 SQLite 导入兼容禁用的 legacy 用户。
- `backend/app/config.py`、`.env.example`：JWT issuer、audience、TTL 和密钥路径配置。
- `.gitignore`：忽略 `secrets/` 和 PEM 文件。

前端：

- `frontend/src/App.tsx`：最小登录、内存 token、Bearer 请求、401 退回登录和客户端退出。
- `frontend/src/App.css`：登录页和退出按钮样式。

测试：

- `backend/tests/conftest.py`：每次测试会话临时生成 RSA 密钥。
- `backend/tests/test_security.py`：Argon2id 与 JWT 单元测试。
- `backend/tests/test_auth_api.py`：注册登录、token 失败、IDOR、文档权限和 inactive 用户。
- `backend/tests/test_phase2_migration_postgres.py`：独立测试数据库上的 `0003 -> 0004` 演练。
- 既有聊天、文档和 PostgreSQL 测试已接入真实认证并修正连接失败语义。

## JWT 设计

- 算法：固定 RS256；签发读取本地私钥，验证只读取公钥。
- claims：强制 `sub`、`iat`、`exp`、`iss`、`aud`。
- 默认 issuer：`customer-intelligence-auth`。
- 默认 audience：`customer-intelligence-api`。
- 默认 TTL：900 秒。
- `sub` 为用户整数 ID 的字符串；验证后再次查询 PostgreSQL，并检查 `is_active`。
- 管理员权限读取数据库当前角色，不信任 token 自带角色。
- 本地 2048 位 RSA 密钥已生成在 `secrets/`，两份 PEM 均被 Git 忽略且未提交。

## 密码与管理员

- 密码使用 `argon2-cffi` 的 Argon2id；数据库从不保存或返回明文。
- 损坏哈希、错误密码均安全返回校验失败。
- 公共注册固定创建 `user`；`role`、`is_admin` 等额外字段返回 422。
- `python -m app.create_admin` 通过 `getpass` 读取密码；已有用户必须输入 `PROMOTE` 才提升。

## 0003 -> 0004 数据保留证据

业务库迁移前：

- revision：`0003`
- conversations：4
- messages：12

业务库迁移后：

- revision：`0004`
- conversations：4
- messages：12
- legacy users：1（inactive）
- `user_id IS NULL`：0

独立数据库 `customer_workbench_phase2_test` 的迁移测试额外构造两个不同 `demo_user_id`、两个会话和两条消息，验证 legacy 用户、归属、内容、NOT NULL 与外键后清理测试数据，结果为 `1 passed`。

## 权限矩阵测试位置

- 注册、登录、当前用户：`test_register_login_and_me`。
- 注册角色注入：`test_registration_rejects_role_injection`。
- 缺失 token：`test_missing_token_protects_all_private_endpoints`。
- 过期、错误签名、issuer/audience 错误：`test_invalid_tokens_return_401_with_bearer_challenge`。
- 两用户会话越权：`test_conversation_owner_can_chat_but_other_user_gets_404`。
- 普通用户检索、普通用户上传 403、管理员上传：`test_document_search_is_authenticated_and_upload_is_admin_only`。
- inactive 用户旧 token：`test_inactive_user_token_is_rejected`。

## 验收结果

| 验收项 | 真实结果 |
| --- | --- |
| Phase 2 核心与既有聊天/文档 API 组合测试 | 52 passed |
| 独立 PostgreSQL `0003 -> 0004` 迁移演练 | 1 passed |
| PostgreSQL + Neo4j + Redis + 独立迁移库后端全量 | 234 passed，0 skipped，5 个第三方 SWIG deprecation warnings |
| 真实 FastAPI 双用户 HTTP 演练 | health 200；注册 201；登录/当前用户 200；本人聊天 200；跨用户 404；普通用户上传 403；未登录检索 401 |
| `backend/.venv/bin/python -m pip check` | No broken requirements found |
| `npm --prefix frontend run build` | 通过 |
| `npm --prefix frontend run lint` | 通过，0 errors |
| `docker compose config --quiet` | 通过 |
| `git diff --check` | 通过 |

最终容器状态：PostgreSQL、Neo4j、Redis 均为 `healthy`。未启动与本阶段无关的 intent-model 容器。

HTTP 演练使用两个随机临时用户和一个临时会话；结束后已精确删除 2 条临时消息、1 个临时会话和 2 个临时用户，原有 4 个会话与 12 条消息未改动。临时 Uvicorn 已正常停止。

## 偏差、限制与未解决问题

- 安全性细化：破坏性迁移演练使用独立的 `TEST_MIGRATION_DATABASE_URL`，避免与普通 `TEST_DATABASE_URL` 指向的业务/集成库混用；数据库名不含 `test` 时测试会拒绝执行。
- 当前只有 15 分钟 Access Token；退出只清理前端内存状态，没有 Refresh Token、服务端撤销或 Redis 黑名单。
- 页面刷新后需要重新登录，符合本阶段约束。
- 没有实现第三方 OAuth、复杂 RBAC、Go Gateway 或限流。
- 无阻塞性未解决问题。

## Git 状态摘要

- 工作区包含本阶段代码、测试、任务单和本报告，均未提交。
- 本地 `secrets/` 密钥文件已被忽略，不出现在 Git 状态中。
- 未 apply、drop 或修改历史 stash；未 commit、未 push。
