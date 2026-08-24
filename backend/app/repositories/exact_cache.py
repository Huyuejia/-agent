"""Redis cache-aside 装饰器：为精确检索增加单键缓存。

实现 `ExactRepository` 协议，在 `SQLAlchemyExactRepository` 之上加一层
Redis 缓存。PostgreSQL 仍是唯一事实来源；Redis 的任何一步失败
（客户端创建 / get / 反序列化 / set）都 fail-open 回退到底层 repository，
不改变聊天行为，也不把未命中降级为相似度检索。

Redis 逻辑不进入 `ExactRetriever` 或 SQLAlchemy 模型；客户端惰性创建，
导入本模块或应用启动时不要求 Redis 在线。
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Protocol, Sequence

from app.retrieval.exact_repository import ExactRepository, ResolvedEntity

logger = logging.getLogger(__name__)

# 缓存 schema 版本；key 前缀本身也带 v1。
_SCHEMA_VERSION = 1
_KEY_PREFIX = "ciw:exact:v1"
_KIND_ENTITY = "entity"
_KIND_MISS = "miss"

# 与 SQLAlchemyExactRepository 分组顺序保持一致（sku → order → serial → error_code）。
_CANONICAL_TYPES = ("sku", "order", "serial", "error_code")


class RedisClient(Protocol):
    """缓存依赖的最小 Redis 客户端接口，便于测试用 fake 替换。"""

    def get(self, name: str) -> bytes | None: ...

    def set(self, name: str, value: bytes, ex: int | None = None) -> object: ...


def redis_key(entity_type: str, normalized_value: str) -> str:
    """单个 (entity_type, normalized_value) 的缓存键。"""
    return f"{_KEY_PREFIX}:{entity_type}:{normalized_value}"


def encode_entity(entity: ResolvedEntity) -> bytes:
    """正命中缓存值：带版本、字段明确的 JSON，可完整还原 ResolvedEntity。"""
    payload = {
        "v": _SCHEMA_VERSION,
        "kind": _KIND_ENTITY,
        "entity_type": entity.entity_type,
        "normalized_value": entity.normalized_value,
        "record_id": entity.record_id,
        "display_identifier": entity.display_identifier,
        "table": entity.table,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def encode_miss() -> bytes:
    """未命中负缓存标记：显式 JSON，而非空值。"""
    return json.dumps({"v": _SCHEMA_VERSION, "kind": _KIND_MISS}).encode("utf-8")


def decode(raw: bytes | None) -> tuple[str, ResolvedEntity | None]:
    """解码缓存值。

    返回 ``(status, entity)``，status ∈ {"miss", "positive", "negative"}：
    - "miss"：需要回源查询 PostgreSQL（含 key 不存在 / JSON 损坏 / 版本不符 / 字段缺失）；
    - "positive"：正命中，携带可用的 ResolvedEntity；
    - "negative"：明确的负缓存命中。
    """
    if raw is None:
        return "miss", None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning("Redis 精确检索缓存 JSON 损坏，降级 PostgreSQL: %s", exc)
        return "miss", None
    if not isinstance(data, dict) or data.get("v") != _SCHEMA_VERSION:
        return "miss", None
    kind = data.get("kind")
    if kind == _KIND_MISS:
        return "negative", None
    if kind == _KIND_ENTITY:
        try:
            entity = ResolvedEntity(
                entity_type=data["entity_type"],
                normalized_value=data["normalized_value"],
                record_id=data["record_id"],
                display_identifier=data["display_identifier"],
                table=data["table"],
            )
        except (KeyError, TypeError) as exc:
            logger.warning(
                "Redis 精确检索缓存实体字段缺失，降级 PostgreSQL: %s", exc
            )
            return "miss", None
        return "positive", entity
    return "miss", None


class RedisExactRepositoryCache(ExactRepository):
    """按 ``(entity_type, normalized_value)`` 粒度做 cache-aside 的装饰器。"""

    def __init__(
        self,
        *,
        repository: ExactRepository,
        redis_client: RedisClient | None = None,
        redis_client_factory: Callable[[], RedisClient] | None = None,
        positive_ttl_seconds: int = 300,
        negative_ttl_seconds: int = 30,
    ) -> None:
        self._repository = repository
        self._redis_client = redis_client
        self._client_factory = redis_client_factory
        self._positive_ttl = positive_ttl_seconds
        self._negative_ttl = negative_ttl_seconds

    def _client(self) -> RedisClient | None:
        """惰性获取 Redis 客户端；失败返回 None 由调用方 fail-open。"""
        if self._redis_client is not None:
            return self._redis_client
        if self._client_factory is None:
            return None
        try:
            self._redis_client = self._client_factory()
        except Exception:
            logger.warning("Redis 客户端创建失败，降级 PostgreSQL", exc_info=True)
            return None
        return self._redis_client

    def _get(self, client: RedisClient, key: str) -> bytes | None:
        try:
            return client.get(key)
        except Exception:
            logger.warning(
                "Redis get 失败，降级 PostgreSQL: key=%s", key, exc_info=True
            )
            return None

    def _set(self, client: RedisClient, key: str, value: bytes, ttl: int) -> None:
        try:
            client.set(key, value, ex=ttl)
        except Exception:
            logger.warning(
                "Redis set 失败（不影响本次结果）: key=%s", key, exc_info=True
            )

    def resolve(
        self,
        entity_types: Sequence[str],
        entity_values: Sequence[str],
    ) -> list[ResolvedEntity]:
        client = self._client()
        if client is None:
            # 无缓存可用：直接回源，行为与底层 repository 完全一致。
            return self._repository.resolve(entity_types, entity_values)

        # 按类型分组并去重，保留首次出现顺序（与底层 IN 查询去重语义一致）。
        grouped: dict[str, list[str]] = {}
        seen: set[tuple[str, str]] = set()
        for entity_type, value in zip(entity_types, entity_values):
            pair = (entity_type, value)
            if pair in seen:
                continue
            seen.add(pair)
            grouped.setdefault(entity_type, []).append(value)

        type_order = [t for t in _CANONICAL_TYPES if t in grouped]

        positive: dict[tuple[str, str], ResolvedEntity] = {}
        negative_hits: set[tuple[str, str]] = set()
        query_types: list[str] = []
        query_values: list[str] = []

        for entity_type in type_order:
            for value in grouped[entity_type]:
                key = redis_key(entity_type, value)
                status, entity = decode(self._get(client, key))
                if status == "positive":
                    # 校验缓存实体与请求 key 一致，防止脏键/串键；不一致则回源。
                    if (
                        entity is not None
                        and entity.entity_type == entity_type
                        and entity.normalized_value == value
                    ):
                        positive[(entity_type, value)] = entity
                    else:
                        logger.warning(
                            "Redis 精确检索缓存实体与 key 不符，回源: key=%s", key
                        )
                        query_types.append(entity_type)
                        query_values.append(value)
                elif status == "negative":
                    negative_hits.add((entity_type, value))
                else:
                    query_types.append(entity_type)
                    query_values.append(value)

        # 仅对未命中（且非负缓存）的键回源，保持批量查询语义。
        db_results: dict[tuple[str, str], ResolvedEntity] = {}
        if query_types:
            for entity in self._repository.resolve(query_types, query_values):
                db_results[(entity.entity_type, entity.normalized_value)] = entity

        result: list[ResolvedEntity] = []
        for entity_type in type_order:
            for value in grouped[entity_type]:
                key = redis_key(entity_type, value)
                pair = (entity_type, value)
                entity = positive.get(pair)
                if entity is not None:
                    result.append(entity)
                    continue
                if pair in negative_hits:
                    # 负缓存命中：不回源，也不重写（避免刷新 TTL）。
                    continue
                entity = db_results.get(pair)
                if entity is not None:
                    result.append(entity)
                    self._set(client, key, encode_entity(entity), self._positive_ttl)
                else:
                    self._set(client, key, encode_miss(), self._negative_ttl)

        return result


def create_redis_client(
    connect_timeout_seconds: float | None = None,
    socket_timeout_seconds: float | None = None,
) -> RedisClient:
    """生产用的惰性、进程内共享 Redis 客户端工厂。

    首次调用才 import redis 并创建客户端（redis-py 的连接在首个命令时才建立，
    因此不会在导入或启动阶段因 Redis 离线而崩溃）。连接/读取超时可配置，
    未显式传入时读取 Settings。
    """
    global _shared_redis_client
    if _shared_redis_client is None:
        import redis

        from app.config import settings

        _shared_redis_client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=False,
            socket_connect_timeout=(
                settings.redis_connect_timeout_seconds
                if connect_timeout_seconds is None
                else connect_timeout_seconds
            ),
            socket_timeout=(
                settings.redis_socket_timeout_seconds
                if socket_timeout_seconds is None
                else socket_timeout_seconds
            ),
        )
    return _shared_redis_client


_shared_redis_client: RedisClient | None = None
