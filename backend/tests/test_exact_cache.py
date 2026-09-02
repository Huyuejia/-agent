"""RedisExactRepositoryCache 单元测试（fake Redis / fake repository，不连外部服务）。"""

from __future__ import annotations

import json

import pytest

from app.repositories.exact_cache import (
    RedisExactRepositoryCache,
    decode,
    encode_entity,
    encode_miss,
    redis_key,
)
from app.retrieval.exact_repository import ResolvedEntity


def _entity(
    entity_type="sku",
    normalized_value="CAM-A1",
    record_id="11111111-1111-1111-1111-111111111111",
    display_identifier="Cam-A1",
    table="products",
    attributes=None,
):
    return ResolvedEntity(
        entity_type,
        normalized_value,
        record_id,
        display_identifier,
        table,
        dict(attributes or {}),
    )


class FakeRedis:
    """内存 fake：按预置数据返回，可注入 get/set 异常，记录调用。"""

    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, bytes, int | None]] = []
        self.get_error: Exception | None = None
        self.set_error: Exception | None = None

    def get(self, name):
        self.get_calls.append(name)
        if self.get_error:
            raise self.get_error
        return self.store.get(name)

    def set(self, name, value, ex=None):
        self.set_calls.append((name, value, ex))
        if self.set_error:
            raise self.set_error
        self.store[name] = value
        return True


class FakeRepository:
    """按 (entity_type, normalized_value) 匹配预置记录，记录调用。"""

    def __init__(self, records=None):
        self._records = list(records or [])
        self.calls: list[tuple[list[str], list[str]]] = []

    def resolve(self, entity_types, entity_values):
        self.calls.append((list(entity_types), list(entity_values)))
        wanted = set(zip(entity_types, entity_values))
        return [
            r for r in self._records if (r.entity_type, r.normalized_value) in wanted
        ]


def test_first_miss_queries_postgres_and_writes_positive_cache():
    redis = FakeRedis()
    repo = FakeRepository([_entity()])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis)

    result = cache.resolve(["sku"], ["CAM-A1"])

    assert result == [_entity()]
    assert repo.calls == [(["sku"], ["CAM-A1"])]
    key = redis_key("sku", "CAM-A1")
    assert redis.set_calls == [(key, encode_entity(_entity()), 300)]
    assert redis.store[key] == encode_entity(_entity())


def test_positive_cache_hit_does_not_query_postgres():
    redis = FakeRedis()
    repo = FakeRepository([_entity()])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis)

    assert cache.resolve(["sku"], ["CAM-A1"]) == [_entity()]
    assert len(repo.calls) == 1

    assert cache.resolve(["sku"], ["CAM-A1"]) == [_entity()]
    # 第二次命中正缓存，不再回源
    assert len(repo.calls) == 1


def test_negative_cache_hit_does_not_query_postgres():
    redis = FakeRedis()
    repo = FakeRepository([])  # 无任何记录
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis)

    assert cache.resolve(["sku"], ["NOT-FOUND"]) == []
    assert len(repo.calls) == 1

    assert cache.resolve(["sku"], ["NOT-FOUND"]) == []
    assert len(repo.calls) == 1  # 负缓存命中，不再回源


def test_ttl_parameters_are_applied():
    redis = FakeRedis()
    repo = FakeRepository([_entity()])
    cache = RedisExactRepositoryCache(
        repository=repo,
        redis_client=redis,
        positive_ttl_seconds=120,
        negative_ttl_seconds=7,
    )

    assert cache.resolve(["sku"], ["CAM-A1"]) == [_entity()]
    assert cache.resolve(["order"], ["ORD-NOPE"]) == []

    set_calls = {name: ex for name, _, ex in redis.set_calls}
    assert set_calls[redis_key("sku", "CAM-A1")] == 120
    assert set_calls[redis_key("order", "ORD-NOPE")] == 7


def test_redis_get_failure_falls_back_to_postgres():
    redis = FakeRedis()
    redis.get_error = RuntimeError("connection refused")
    repo = FakeRepository([_entity()])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis)

    result = cache.resolve(["sku"], ["CAM-A1"])

    assert result == [_entity()]
    assert repo.calls == [(["sku"], ["CAM-A1"])]


def test_redis_set_failure_does_not_affect_postgres_result():
    redis = FakeRedis()
    repo = FakeRepository([_entity()])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis)

    result = cache.resolve(["sku"], ["CAM-A1"])
    assert result == [_entity()]

    redis.set_error = RuntimeError("write failed")
    # 即使 set 失败，结果仍应来自 PostgreSQL 且不抛异常
    result = cache.resolve(["order"], ["ORD-NOPE"])
    assert result == []
    assert len(repo.calls) == 2


def test_corrupt_json_falls_back_to_postgres():
    redis = FakeRedis()
    repo = FakeRepository([_entity()])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis)

    redis.store[redis_key("sku", "CAM-A1")] = b"{not valid json!!"

    result = cache.resolve(["sku"], ["CAM-A1"])

    assert result == [_entity()]
    assert repo.calls == [(["sku"], ["CAM-A1"])]
    # 回源后重写正确缓存
    assert redis.store[redis_key("sku", "CAM-A1")] == encode_entity(_entity())


def test_mixed_entities_duplicates_and_result_order():
    sku = _entity()
    order = _entity(
        entity_type="order",
        normalized_value="ORD100001",
        record_id="22222222-2222-2222-2222-222222222222",
        display_identifier="ORD-100001",
        table="orders",
    )
    redis = FakeRedis()
    repo = FakeRepository([sku, order])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis)

    # 混合类型 + 重复 sku 值
    result = cache.resolve(
        ["sku", "order", "sku"],
        ["CAM-A1", "ORD100001", "CAM-A1"],
    )

    assert result == [sku, order]
    # 去重后仅回源 sku 与 order 各一次
    assert repo.calls == [(["sku", "order"], ["CAM-A1", "ORD100001"])]

    # 热缓存后再查，顺序稳定且不回源
    result = cache.resolve(
        ["order", "sku"],
        ["ORD100001", "CAM-A1"],
    )
    assert result == [sku, order]
    assert len(repo.calls) == 1


def test_missing_entity_writes_explicit_negative_marker():
    redis = FakeRedis()
    cache = RedisExactRepositoryCache(
        repository=FakeRepository([]), redis_client=redis
    )

    assert cache.resolve(["sku"], ["GHOST"]) == []

    raw = redis.store[redis_key("sku", "GHOST")]
    data = json.loads(raw.decode("utf-8"))
    assert data["v"] == 2
    assert data["kind"] == "miss"
    assert decode(raw) == ("negative", None)


def test_no_redis_client_falls_back_to_repository():
    # 未提供 redis_client / factory 时，直接回源且不抛异常
    repo = FakeRepository([_entity()])
    cache = RedisExactRepositoryCache(repository=repo)

    assert cache.resolve(["sku"], ["CAM-A1"]) == [_entity()]
    assert repo.calls == [(["sku"], ["CAM-A1"])]


def test_client_factory_failure_falls_back_to_repository():
    repo = FakeRepository([_entity()])

    def broken_factory():
        raise RuntimeError("redis unavailable at startup")

    cache = RedisExactRepositoryCache(
        repository=repo, redis_client_factory=broken_factory
    )

    assert cache.resolve(["sku"], ["CAM-A1"]) == [_entity()]
    assert repo.calls == [(["sku"], ["CAM-A1"])]


def test_encode_decode_roundtrip_and_miss_marker():
    entity = _entity()
    assert decode(encode_entity(entity)) == ("positive", entity)
    assert decode(encode_miss()) == ("negative", None)
    assert decode(None) == ("miss", None)
    # 未知版本按 miss 处理（回源重写）
    unknown = json.dumps(
        {"v": 99, "kind": "entity", "entity_type": "sku", "normalized_value": "X",
         "record_id": "r", "display_identifier": "x", "table": "products"}
    ).encode()
    assert decode(unknown) == ("miss", None)


def test_encode_decode_preserves_business_attributes():
    entity = _entity(
        entity_type="error_code",
        normalized_value="E1001",
        display_identifier="E1001",
        table="error_codes",
        attributes={
            "message": "设备离线",
            "resolution": "检查设备电源和网络连接",
        },
    )

    assert decode(encode_entity(entity)) == ("positive", entity)


def test_negative_cache_hit_does_not_refresh_ttl():
    redis = FakeRedis()
    repo = FakeRepository([])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis)

    assert cache.resolve(["sku"], ["NOT-FOUND"]) == []
    assert len(redis.set_calls) == 1  # 首次写入负缓存

    redis.set_calls.clear()
    assert cache.resolve(["sku"], ["NOT-FOUND"]) == []
    # 负缓存命中：不回源，也不重写（不刷新 TTL）
    assert redis.set_calls == []
    assert len(repo.calls) == 1


def test_positive_cache_entity_type_mismatch_falls_back():
    redis = FakeRedis()
    repo = FakeRepository([_entity()])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis)

    # 预置一个 entity_type 与 key 不符的实体
    wrong = _entity(
        entity_type="order",
        normalized_value="CAM-A1",
        record_id="22222222-2222-2222-2222-222222222222",
        display_identifier="Cam-A1",
        table="orders",
    )
    key = redis_key("sku", "CAM-A1")
    redis.store[key] = encode_entity(wrong)

    result = cache.resolve(["sku"], ["CAM-A1"])

    # 应回源，返回正确实体，并重写缓存
    assert result == [_entity()]
    assert repo.calls == [(["sku"], ["CAM-A1"])]
    assert redis.store[key] == encode_entity(_entity())


def test_positive_cache_normalized_value_mismatch_falls_back():
    redis = FakeRedis()
    repo = FakeRepository([_entity()])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis)

    # 预置一个 normalized_value 与 key 不符的实体
    wrong = _entity(normalized_value="SOME-OTHER-VALUE")
    key = redis_key("sku", "CAM-A1")
    redis.store[key] = encode_entity(wrong)

    result = cache.resolve(["sku"], ["CAM-A1"])

    assert result == [_entity()]
    assert repo.calls == [(["sku"], ["CAM-A1"])]
    assert redis.store[key] == encode_entity(_entity())


def test_create_redis_client_applies_timeouts(monkeypatch):
    import sys
    from types import SimpleNamespace

    import app.repositories.exact_cache as module

    monkeypatch.setattr(module, "_shared_redis_client", None)

    captured = {}

    class FakeRedis:
        @staticmethod
        def from_url(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return "fake-client"

    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=FakeRedis))

    client = module.create_redis_client(
        connect_timeout_seconds=1.5, socket_timeout_seconds=3.5
    )

    assert client == "fake-client"
    assert captured["socket_connect_timeout"] == 1.5
    assert captured["socket_timeout"] == 3.5
    assert captured["decode_responses"] is False
