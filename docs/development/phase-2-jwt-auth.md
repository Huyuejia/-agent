# Phase 2：JWT 身份认证与权限闭环

## 开工基线

- 分支：`feat/hybrid-retrieval`
- 基线提交：`cebba1b feat: add Redis exact retrieval cache`
- Codex 审计时工作区 clean，历史 `stash@{0}` 仍存在；不得 apply、drop 或修改该 stash。
- 当前代码迁移 head 为 `0003`。Docker Desktop 引擎在审计时未运行，因此数据库实际 revision 和容器健康状态尚未在线确认；开工后先只读确认，不得把历史报告当成本轮结果。
- 开工前运行 `git status --short --branch`；若除本任务单外还有未知改动，停止并报告。

## 阶段目标

建立真实的用户身份和资源权限边界：用户以安全哈希密码注册和登录，Python API 签发并验证短期 RS256 Access JWT；会话绑定当前用户，聊天阻止跨用户访问；普通用户可聊天和直接检索文档，只有管理员可上传文档；前端提供最小登录流程并在内存中持有 token。

本阶段不是只增加 token 编解码函数。认证、会话归属和管理员授权必须形成闭环。

## 当前缺口（已核验）

- 没有 `users` 表、用户模型、认证路由、JWT 或密码哈希代码。
- `Conversation.demo_user_id` 是整数占位字段，没有用户外键。
- `POST /api/conversations` 和 `POST /api/chat` 不要求登录；聊天只检查会话 ID 是否存在。
- `POST /api/documents` 和 `POST /api/documents/search` 均未认证。
- 前端直接调用公开 API，没有登录状态或 Bearer Token。
- 现有 PostgreSQL 集成测试中，`test_document_retrieval_postgres.py` 和 `test_exact_repository_postgres.py` 在已设置 `TEST_DATABASE_URL` 但连接失败时仍会 skip，必须在本阶段修正为 fail。

## 固定安全设计

1. JWT 使用 **RS256**。Python 认证模块用私钥签发，Python API 用公钥验证；未来 Go Gateway 只需获得公钥。不得改成 HS256，除非先停止并说明跨服务共享密钥的影响。
2. Access Token 有效期默认 15 分钟；固定允许算法列表为 `['RS256']`，不得信任 token header 自报算法。
3. 必须校验 `sub`、`exp`、`iss`、`aud`；同时签发 `iat`。固定默认值：
   - issuer：`customer-intelligence-auth`
   - audience：`customer-intelligence-api`
   - `sub`：用户整数 ID 的字符串形式
4. 密码使用 `argon2-cffi` 的 Argon2id 哈希；数据库只保存哈希。注册密码长度 8～128 个字符，不做可逆加密。
5. 私钥、公钥和真实 `.env` 不提交 Git。配置只保存密钥文件路径；测试使用临时生成的 RSA 密钥，不依赖开发者本机密钥。
6. `get_current_user` 在验证 JWT 后必须按 `sub` 查询数据库并确认用户仍为 active；管理员判断使用数据库当前角色，不只信任 token 内角色。
7. 认证失败统一返回 401，并带 `WWW-Authenticate: Bearer`；已认证但权限不足返回 403。
8. 跨用户会话与不存在会话都返回 404，避免通过状态差异枚举他人会话。
9. 注册请求 schema 使用 `extra='forbid'`；请求中出现 `role`、`is_admin` 等越权字段返回 422。所有公开注册用户固定为 `user`。
10. 登录失败使用统一错误信息，不区分“账号不存在”和“密码错误”。

## 固定依赖与代码边界

- 在 `backend/requirements.txt` 增加并固定实际测试通过的 `PyJWT[crypto]`、`argon2-cffi`；若使用 `EmailStr`，同时固定 `email-validator`。
- 新增并注册以下职责，不把认证逻辑塞进现有检索器、编排器或 SQLAlchemy 模型：
  - `backend/app/models/user.py`：用户持久化模型；
  - `backend/app/schemas/auth.py`：注册、登录、token、当前用户 schema；
  - `backend/app/security/passwords.py`：哈希与校验；
  - `backend/app/security/jwt.py`：RS256 签发与严格验证；
  - `backend/app/dependencies/auth.py`：`get_current_user`、`require_admin`；
  - `backend/app/api/auth.py`：认证接口；
  - `backend/app/create_admin.py`：交互式管理员创建/提升工具，密码通过 `getpass` 读取，不允许明文命令行参数。
- 可按现有结构微调文件名，但职责必须单一，不能创建第二套数据库 Session 或应用工厂。
- `backend/app/models/__init__.py` 和 `backend/alembic/env.py` 必须导入 User 模型，使统一 metadata 完整。

## 固定数据模型

### users

| 字段 | 类型与约束 | 说明 |
| --- | --- | --- |
| `id` | Integer，自增主键 | JWT `sub` 的真实来源 |
| `email` | String(320)，非空 | 展示用邮箱，保存清理后的值 |
| `normalized_email` | String(320)，非空，唯一索引 | `strip().lower()` 后用于注册/登录查找 |
| `password_hash` | Text，非空 | Argon2id 哈希；绝不返回 API |
| `role` | String(16)，非空，默认 `user`，CHECK | 只允许 `user`、`admin` |
| `is_active` | Boolean，非空，默认 true | 禁用后旧 token 也不可继续使用 |
| `created_at` | timezone-aware DateTime，数据库默认 now | 创建时间 |
| `updated_at` | timezone-aware DateTime，数据库默认 now | 更新时间 |

### conversations

- 删除模型中的 `demo_user_id`。
- 新增 `user_id Integer NOT NULL REFERENCES users(id)` 和普通索引。
- `orders.user_id` 目前是演示订单的字符串业务字段，本阶段不与认证用户表合并，避免扩大范围。

## 固定 Alembic 迁移策略

新增 `0004`，`down_revision = '0003'`，禁止修改已应用的 `0001`～`0003`。

升级按以下顺序进行，保留全部现有会话与消息：

1. 创建 `users` 表、约束和唯一索引。
2. 对 `conversations` 中每个不同的 `demo_user_id` 创建一个禁用的 legacy 用户，尽量沿用该整数作为 `users.id`；邮箱使用不会对外投递的 `legacy-<id>@local.invalid`，角色为 `user`，`is_active=false`，密码哈希使用明确的不可登录占位值。
3. 给 `conversations` 增加暂时可空的 `user_id`。
4. 按原 `demo_user_id` 回填 `user_id`，并在迁移内断言不存在未回填行。
5. 将 `user_id` 改为非空，添加外键和索引，再删除 `demo_user_id`。
6. 校正 users 自增序列，避免新注册用户 ID 与 legacy ID 冲突。

迁移不得删表重建 conversations/messages，不得删除、重编号或改写历史消息。downgrade 只在明确的可丢弃测试库验证；正常开发验收不得对现有数据执行 downgrade。

迁移测试必须先在显式的可丢弃 PostgreSQL 测试库升级到 `0003`，插入至少两个不同 `demo_user_id` 的会话及消息，再升级到 head，断言：

- Alembic revision 到达 `0004`；
- 两个 legacy 用户均存在且不可登录；
- 每个会话归属保持正确；
- 会话和消息数量、内容均未丢失；
- `demo_user_id` 已删除，`user_id` 为 NOT NULL 且有外键。

## 固定接口

| 方法与路径 | 请求 | 成功响应 | 约束 |
| --- | --- | --- | --- |
| `POST /api/auth/register` | JSON：`email`、`password` | 201：`id`、`email`、`role` | 公开；角色永远为 `user`；重复邮箱 409 |
| `POST /api/auth/login` | JSON：`email`、`password` | 200：`access_token`、`token_type='bearer'`、`expires_in=900` | 公开；错误凭据或 inactive 用户统一 401 |
| `GET /api/auth/me` | Bearer Token | 200：`id`、`email`、`role` | 登录用户 |
| `POST /api/conversations` | 保持现有 title query 契约 | 保持现有 201 响应 | 登录用户；服务端自动写当前 `user_id` |
| `POST /api/chat` | 保持现有 JSON 契约 | 保持现有 200 响应 | 登录用户；只允许访问自己的 conversation |
| `POST /api/documents/search` | 保持现有 JSON 契约 | 保持现有 200 响应 | 任意登录用户 |
| `POST /api/documents` | 保持现有 multipart 契约 | 保持现有 201 响应 | 仅 admin |
| `GET /health` | 无 | 保持现有 200 响应 | 公开 |

除上述认证变化外，不改变聊天、检索、上传响应结构，不增加无必要的新会话 CRUD 接口。

## 权限矩阵

| 能力 | 未登录 | 普通用户 | 管理员 |
| --- | ---: | ---: | ---: |
| 注册、登录、health | 允许 | 允许 | 允许 |
| 查询当前用户 | 401 | 允许 | 允许 |
| 创建自己的会话 | 401 | 允许 | 允许 |
| 在自己的会话聊天 | 401 | 允许 | 允许 |
| 访问他人会话 | 401 | 404 | 404 |
| 直接文档检索 | 401 | 允许 | 允许 |
| 上传文档 | 401 | 403 | 允许 |

## 前端最小范围

1. 增加邮箱/密码登录界面；登录成功后将 Access Token 只保存在 React 内存状态。
2. 新建会话和聊天请求都携带 `Authorization: Bearer <token>`。
3. 401 时清除内存 token 并回到登录界面；提供“退出”按钮，只清理客户端状态。
4. 不使用 `localStorage`、`sessionStorage`、Cookie 或 IndexedDB 长期保存 Access Token。
5. 页面刷新后需要重新登录，这是本阶段接受的限制；在阶段报告中明确记录。
6. 本阶段不要求管理员上传 UI，管理员能力可通过 API 测试和学习实验验证。

## 管理员创建方式

- 公共注册接口绝不接受角色。
- `python -m app.create_admin` 通过交互式邮箱和 `getpass` 密码创建管理员；若邮箱已存在，只能在明确确认后提升角色。
- 工具复用同一 Argon2id 哈希函数和数据库 Session，不打印密码或哈希。
- 测试可以直接在测试事务中创建 admin fixture，不依赖 CLI。

## 必须测试

### 单元测试

- Argon2id：哈希不等于明文、正确密码通过、错误密码失败、损坏哈希安全失败。
- JWT：正常签发/验证；缺失 `sub`；过期；错误签名；错误 issuer；错误 audience；非 RS256 算法全部失败。
- 配置：TTL 必须为正；密钥路径缺失时认证操作明确失败，不能静默生成或回退共享密钥。

### API 认证与授权测试

- 注册固定为普通用户；请求带 `role=admin` 或 `is_admin=true` 返回 422；重复邮箱 409。
- 登录、`/api/auth/me`、缺 token、格式错误 token、过期 token、错误签名、issuer/audience 错误。
- 用户 A 创建会话后能聊天；用户 B 使用 A 的 `conversation_id` 返回 404，且不产生任何 Message。
- 不存在会话返回 404。
- 未登录创建会话/聊天/文档检索/上传均返回 401。
- 普通用户文档检索成功、上传返回 403；管理员上传成功。
- inactive 用户即使持有尚未过期的 token 也返回 401。

### PostgreSQL 与回归测试

- 增加上述真实 PostgreSQL `0003 -> 0004` 数据保留迁移测试。
- 更新现有聊天与文档 API 测试，显式创建用户并携带 token；不能通过关闭认证来让旧测试通过。
- 保留并运行 EXACT、GRAPH、LEXICAL、VECTOR、HYBRID、Redis 缓存及现有 API 回归测试。
- 修正已有 PostgreSQL fixture：只有 `TEST_DATABASE_URL` 未设置时允许 skip；变量已设置但连接失败必须 fail。
- 测试日志必须分别报告 passed 和 skipped，不能复制历史 `214 passed`。

## 验收命令

CC 应根据实际环境执行并在报告中记录原始结果；不得伪造 Docker/GPU/集成测试状态：

```bash
git status --short --branch
docker compose config
docker compose up -d postgres neo4j redis
docker compose ps
backend/.venv/bin/python -m pip check
backend/.venv/bin/python -m pytest -s backend/tests/<JWT与密码单元测试>
backend/.venv/bin/python -m pytest -s backend/tests/<认证API测试>
TEST_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/customer_workbench_phase2_test backend/.venv/bin/python -m pytest -s backend/tests/<Phase-2迁移测试>
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/customer_workbench TEST_REDIS_URL=redis://localhost:6379/0 TEST_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/customer_workbench_phase2_test backend/.venv/bin/python -m pytest -s backend/tests
npm --prefix frontend run build
npm --prefix frontend run lint
git diff --check
git status --short
git diff --stat
```

若迁移测试需要重建 schema，必须使用单独命名、明确可丢弃的测试数据库，不能清空当前 `customer_workbench` 数据库。

## 禁止事项

- 不执行 `docker compose down -v`，不删除 `postgres-data`、`neo4j-data`，不清空业务数据库。
- 不修改或压平既有 Alembic 迁移，不删表重建会话，不做破坏性 Git reset。
- 不提交 `.env`、私钥、公钥、密码、token 或测试生成密钥；补充 `.gitignore` 覆盖本地密钥目录。
- 不实现 Refresh Token、Redis token 黑名单、第三方 OAuth、复杂 RBAC、Cookie 会话或服务端 logout。
- 不开始 Go Gateway、限流、Kubernetes、消息队列或 reranker。
- 不改 PostgreSQL/pgvector、Neo4j、Redis 的既有职责，不把认证数据放进 Redis。
- 不 commit、不 push；完成实现、测试和报告后停止，等待 Codex 审计。

## 完成报告

写入 `docs/development/reports/phase-2-jwt-auth-report.md`，只记录：

- 改动文件及职责；
- JWT 算法、claims、TTL、密钥加载方式；
- 密码哈希与管理员创建方式；
- `0003 -> 0004` 数据保留证据；
- 权限矩阵对应测试位置；
- 各条验收命令的真实 passed/skipped/failed 数和 Docker 状态；
- 与任务单的偏差、未解决问题；
- `git status --short` 与 `git diff --stat`。

报告写完后停止，不 commit，不自行开始 Phase 3。
