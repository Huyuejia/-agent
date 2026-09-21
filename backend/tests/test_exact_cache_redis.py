"""RedisExactRepositoryCache 真实 Redis 集成测试。

- 通过 TEST_REDIS_URL 显式启用
- 未设置或 Redis 不可用时明确 skip（不伪装为 passed）
- 标记：integration
"""

from __future__ import annotations

import os

import pytest

from app.repositories.exact_cache import RedisExactRepositoryCache, redis_key
from app.retrieval.exact_repository import ResolvedEntity

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def redis_client():
    if not TEST_REDIS_URL:
        pytest.skip("TEST_REDIS_URL 未设置，跳过 Redis 集成测试")

    import redis

    client = redis.Redis.from_url(TEST_REDIS_URL)
    yield client
    client.close()


def test_redis_connection(redis_client):
    # TEST_REDIS_URL 已设置时，连接失败必须使测试失败（而非跳过）。
    assert redis_client.ping() is True


class CountingRepository:
    """按 (entity_type, normalized_value) 匹配记录并计数调用。"""

    def __init__(self, records):
        self._records = list(records)
        self.calls = 0

    def resolve(self, entity_types, entity_values):
        self.calls += 1
        wanted = set(zip(entity_types, entity_values))
        return [
            r for r in self._records if (r.entity_type, r.normalized_value) in wanted
        ]


def test_positive_cache_roundtrip(redis_client):
    entity = ResolvedEntity(
        entity_type="sku",
        normalized_value="ITEST-SKU-1",
        record_id="itest-rec-1",
        display_identifier="Itest-Sku-1",
        table="products",
    )
    repo = CountingRepository([entity])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis_client)
    key = redis_key("sku", "ITEST-SKU-1")

    try:
        first = cache.resolve(["sku"], ["ITEST-SKU-1"])
        assert first == [entity]
        assert repo.calls == 1

        second = cache.resolve(["sku"], ["ITEST-SKU-1"])
        assert second == [entity]
        assert repo.calls == 1  # 正缓存命中，不回源

        # 校验真实 Redis 中落盘的值是带版本的正缓存 JSON
        from app.repositories.exact_cache import decode

        assert decode(redis_client.get(key)) == ("positive", entity)
    finally:
        redis_client.delete(key)


def test_negative_cache_roundtrip(redis_client):
    repo = CountingRepository([])
    cache = RedisExactRepositoryCache(repository=repo, redis_client=redis_client)
    key = redis_key("sku", "ITEST-MISS-1")

    try:
        assert cache.resolve(["sku"], ["ITEST-MISS-1"]) == []
        assert repo.calls == 1

        assert cache.resolve(["sku"], ["ITEST-MISS-1"]) == []
        assert repo.calls == 1  # 负缓存命中，不回源
    finally:
        redis_client.delete(key)
