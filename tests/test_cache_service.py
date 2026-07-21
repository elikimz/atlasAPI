"""Unit coverage for Atlas' resilient cache and invalidation contract."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.config import settings
from app.services.cache import (
    CacheKeys,
    cache,
    invalidate_shared_cache,
    invalidate_user_cache,
)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
async def isolated_local_cache() -> AsyncIterator[None]:
    """Force the bounded in-memory tier so tests never need a Redis service."""
    original_enabled = settings.CACHE_ENABLED
    original_disabled = cache._redis_disabled
    original_redis = cache._redis
    settings.CACHE_ENABLED = True
    cache._redis_disabled = True
    cache._redis = None
    await cache.reset_for_tests()
    try:
        yield
    finally:
        await cache.reset_for_tests()
        settings.CACHE_ENABLED = original_enabled
        cache._redis_disabled = original_disabled
        cache._redis = original_redis


@pytest.mark.anyio
async def test_get_or_set_caches_one_loader_result() -> None:
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"value": 42}

    first = await cache.get_or_set("atlas:test:hit", 60, loader)
    second = await cache.get_or_set("atlas:test:hit", 60, loader)

    assert first == {"value": 42}
    assert second == {"value": 42}
    assert calls == 1


@pytest.mark.anyio
async def test_concurrent_cache_miss_is_coalesced_per_key() -> None:
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"generation": calls}

    results = await asyncio.gather(
        *(cache.get_or_set("atlas:test:coalesced", 60, loader) for _ in range(12))
    )

    assert results == [{"generation": 1}] * 12
    assert calls == 1


@pytest.mark.anyio
async def test_user_invalidation_is_scoped_to_requested_groups() -> None:
    user_id = 77
    other_user_id = 78
    dashboard_key = CacheKeys.user_dashboard(user_id)
    task_key = CacheKeys.user_available_tasks(user_id)
    payment_key = CacheKeys.user_payment_overview(user_id)
    other_dashboard_key = CacheKeys.user_dashboard(other_user_id)

    await cache.set(dashboard_key, {"dashboard": True}, 60)
    await cache.set(task_key, [{"id": 1}], 60)
    await cache.set(payment_key, {"total": 9}, 60)
    await cache.set(other_dashboard_key, {"dashboard": True}, 60)

    await invalidate_user_cache(user_id, "dashboard", "tasks")

    assert await cache.get(dashboard_key) is None
    assert await cache.get(task_key) is None
    assert await cache.get(payment_key) == {"total": 9}
    assert await cache.get(other_dashboard_key) == {"dashboard": True}


@pytest.mark.anyio
async def test_shared_invalidation_does_not_flush_other_shared_values() -> None:
    plan_key = CacheKeys.plans()
    config_key = CacheKeys.app_config()
    stats_key = CacheKeys.admin_stats()
    await cache.set(plan_key, [{"id": 1}], 60)
    await cache.set(config_key, [{"key": "support_ticket_url"}], 60)
    await cache.set(stats_key, {"total_users": 1}, 60)

    await invalidate_shared_cache("plans", "admin_stats")

    assert await cache.get(plan_key) is None
    assert await cache.get(stats_key) is None
    assert await cache.get(config_key) == [{"key": "support_ticket_url"}]
