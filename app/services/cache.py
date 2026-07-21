"""Resilient cache primitives for Atlas API.

Redis is the production shared-cache implementation. When it is intentionally not
configured (local development/tests) or becomes temporarily unavailable, the
service falls back to a bounded in-process TTL cache so that caching never
becomes an availability dependency.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar

from app.config import settings

try:  # pragma: no cover - exercised through the fallback when Redis is absent.
    from redis.asyncio import Redis
    from redis.exceptions import RedisError
except ImportError:  # Keeps unit tests usable before optional runtime installation.
    Redis = Any  # type: ignore[misc,assignment]

    class RedisError(Exception):
        """Fallback error type when redis-py is not yet installed."""


logger = logging.getLogger(__name__)
T = TypeVar("T")


class CacheTTL:
    """Central TTL policy in seconds."""

    PLANS = 30 * 60
    APP_CONFIG = 60 * 60
    PROFILE = 10 * 60
    TASKS = 5 * 60
    DASHBOARD = 30
    REFERRALS = 5 * 60
    PAYMENTS = 60
    NOTIFICATIONS = 20
    ADMIN_STATS = 30


class CacheKeys:
    """Stable cache-key constructors. Never include credentials or raw tokens."""

    @staticmethod
    def plans() -> str:
        return "atlas:plans:active"

    @staticmethod
    def app_config() -> str:
        return "atlas:app-config"

    @staticmethod
    def admin_stats() -> str:
        return "atlas:admin:stats"

    @staticmethod
    def user_prefix(user_id: int) -> str:
        return f"atlas:user:{user_id}"

    @classmethod
    def user_dashboard(cls, user_id: int) -> str:
        return f"{cls.user_prefix(user_id)}:dashboard"

    @classmethod
    def user_available_tasks(cls, user_id: int) -> str:
        return f"{cls.user_prefix(user_id)}:tasks:available"

    @classmethod
    def user_all_tasks(cls, user_id: int) -> str:
        return f"{cls.user_prefix(user_id)}:tasks:all"

    @classmethod
    def user_referral_summary(cls, user_id: int) -> str:
        return f"{cls.user_prefix(user_id)}:referrals:summary"

    @classmethod
    def user_referral_codes(cls, user_id: int) -> str:
        return f"{cls.user_prefix(user_id)}:referrals:codes"

    @classmethod
    def user_active_referrals(cls, user_id: int) -> str:
        return f"{cls.user_prefix(user_id)}:referrals:active"

    @classmethod
    def user_payments(cls, user_id: int, page: int, limit: int) -> str:
        return f"{cls.user_prefix(user_id)}:payments:history:{page}:{limit}"

    @classmethod
    def user_payment_overview(cls, user_id: int) -> str:
        return f"{cls.user_prefix(user_id)}:payments:overview"

    @classmethod
    def user_notifications(cls, user_id: int, page: int, limit: int) -> str:
        return f"{cls.user_prefix(user_id)}:notifications:{page}:{limit}"


class CacheService:
    """JSON cache with a Redis primary and bounded local TTL fallback."""

    def __init__(self) -> None:
        self._redis: Redis | None = None
        self._redis_disabled = False
        self._memory: dict[str, tuple[float, str]] = {}
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return settings.CACHE_ENABLED

    @staticmethod
    def _serialize(value: Any) -> str:
        return json.dumps(value, default=CacheService._json_default, separators=(",", ":"))

    @staticmethod
    def _json_default(value: Any) -> str | float:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    async def _redis_client(self) -> Redis | None:
        if not self.enabled or self._redis_disabled or not settings.REDIS_URL:
            return None
        if self._redis is not None:
            return self._redis

        async with self._lock:
            if self._redis is not None or self._redis_disabled:
                return self._redis
            try:
                self._redis = Redis.from_url(
                    settings.REDIS_URL,
                    decode_responses=True,
                    socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
                    socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
                    health_check_interval=30,
                )
                await self._redis.ping()
                logger.info("cache.redis.connected")
            except (RedisError, OSError, ValueError) as exc:
                logger.warning("cache.redis.unavailable; using process-local fallback: %s", exc)
                self._redis = None
                self._redis_disabled = True
        return self._redis

    async def _memory_get(self, key: str) -> Any | None:
        now = time.monotonic()
        async with self._lock:
            item = self._memory.get(key)
            if item is None:
                return None
            expires_at, raw = item
            if expires_at <= now:
                self._memory.pop(key, None)
                return None
            return json.loads(raw)

    async def _memory_set(self, key: str, value: Any, ttl_seconds: int) -> None:
        raw = self._serialize(value)
        async with self._lock:
            if len(self._memory) >= settings.CACHE_FALLBACK_MAX_ENTRIES:
                # Expire old entries first; if all remain fresh, evict the earliest expiry.
                now = time.monotonic()
                expired = [memory_key for memory_key, (expires_at, _) in self._memory.items() if expires_at <= now]
                for memory_key in expired:
                    self._memory.pop(memory_key, None)
                if len(self._memory) >= settings.CACHE_FALLBACK_MAX_ENTRIES:
                    evicted_key = min(self._memory, key=lambda memory_key: self._memory[memory_key][0])
                    self._memory.pop(evicted_key, None)
            self._memory[key] = (time.monotonic() + ttl_seconds, raw)

    async def get(self, key: str) -> Any | None:
        """Return a decoded cache value, or ``None`` on miss/error."""
        if not self.enabled:
            return None
        redis_client = await self._redis_client()
        if redis_client is not None:
            try:
                raw = await redis_client.get(key)
                if raw is not None:
                    logger.debug("cache.hit key=%s source=redis", key)
                    return json.loads(raw)
            except (RedisError, OSError, json.JSONDecodeError) as exc:
                logger.warning("cache.redis.get_failed key=%s error=%s", key, exc)
        value = await self._memory_get(key)
        logger.debug("cache.%s key=%s source=memory", "hit" if value is not None else "miss", key)
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Store a JSON-serializable response without raising cache failures."""
        if not self.enabled or ttl_seconds <= 0:
            return
        try:
            raw = self._serialize(value)
        except (TypeError, ValueError) as exc:
            logger.warning("cache.serialize_failed key=%s error=%s", key, exc)
            return

        redis_client = await self._redis_client()
        if redis_client is not None:
            try:
                await redis_client.set(key, raw, ex=ttl_seconds)
                logger.debug("cache.set key=%s source=redis", key)
                return
            except (RedisError, OSError) as exc:
                logger.warning("cache.redis.set_failed key=%s error=%s", key, exc)
        await self._memory_set(key, value, ttl_seconds)
        logger.debug("cache.set key=%s source=memory", key)

    async def delete(self, *keys: str) -> None:
        """Remove exact keys from both cache tiers."""
        keys = tuple(key for key in keys if key)
        if not keys:
            return
        redis_client = await self._redis_client()
        if redis_client is not None:
            try:
                await redis_client.delete(*keys)
            except (RedisError, OSError) as exc:
                logger.warning("cache.redis.delete_failed keys=%s error=%s", keys, exc)
        async with self._lock:
            for key in keys:
                self._memory.pop(key, None)
        logger.debug("cache.delete keys=%s", keys)

    async def delete_pattern(self, pattern: str) -> None:
        """Remove keys matching a limited, application-controlled pattern."""
        redis_client = await self._redis_client()
        if redis_client is not None:
            try:
                batch: list[str] = []
                async for key in redis_client.scan_iter(match=pattern, count=100):
                    batch.append(key)
                    if len(batch) >= 100:
                        await redis_client.delete(*batch)
                        batch.clear()
                if batch:
                    await redis_client.delete(*batch)
            except (RedisError, OSError) as exc:
                logger.warning("cache.redis.delete_pattern_failed pattern=%s error=%s", pattern, exc)
        async with self._lock:
            for key in [memory_key for memory_key in self._memory if fnmatch.fnmatch(memory_key, pattern)]:
                self._memory.pop(key, None)
        logger.debug("cache.delete_pattern pattern=%s", pattern)

    async def get_or_set(self, key: str, ttl_seconds: int, loader: Callable[[], Awaitable[T]]) -> T:
        """Deduplicate concurrent misses for one process and cache successful loads."""
        cached = await self.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        async with self._lock:
            key_lock = self._key_locks.setdefault(key, asyncio.Lock())
        async with key_lock:
            cached = await self.get(key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            value = await loader()
            await self.set(key, value, ttl_seconds)
            return value

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except (RedisError, OSError):
                logger.warning("cache.redis.close_failed", exc_info=True)
        self._redis = None

    async def reset_for_tests(self) -> None:
        """Clear all cache state for isolated test fixtures."""
        async with self._lock:
            self._memory.clear()
            self._key_locks.clear()


cache = CacheService()


async def invalidate_user_cache(user_id: int, *groups: str) -> None:
    """Invalidate user-scoped cache groups after a committed write."""
    prefix = CacheKeys.user_prefix(user_id)
    group_patterns = {
        "dashboard": f"{prefix}:dashboard",
        "tasks": f"{prefix}:tasks:*",
        "referrals": f"{prefix}:referrals:*",
        "payments": f"{prefix}:payments:*",
        "notifications": f"{prefix}:notifications:*",
        "all": f"{prefix}:*",
    }
    patterns = [group_patterns[group] for group in groups if group in group_patterns]
    for pattern in patterns:
        await cache.delete_pattern(pattern)


async def invalidate_shared_cache(*groups: str) -> None:
    """Invalidate shared non-user cache groups after a committed write."""
    keys = {
        "plans": CacheKeys.plans(),
        "app_config": CacheKeys.app_config(),
        "admin_stats": CacheKeys.admin_stats(),
    }
    await cache.delete(*(keys[group] for group in groups if group in keys))
